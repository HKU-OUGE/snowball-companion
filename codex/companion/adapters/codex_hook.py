#!/usr/bin/env python3
"""Privacy-preserving Codex hook/JSONL adapter. Standard library; never reads transcripts."""
import argparse
from contextlib import contextmanager, closing
import fcntl
import hashlib
import json
import os
import re
from pathlib import Path
import shlex
import sqlite3
import unicodedata
import sys
import tempfile
import time
import urllib.request
import urllib.error
import uuid

HOOKS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest',
         'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd', 'PreCompact', 'PostCompact')
MAX_INPUT = 2 * 1024 * 1024
DEFAULT_STATE = Path.home() / '.local/state/xueqiu/codex-adapter'
DEFAULT_TOKEN = Path.home() / '.config/xueqiu/hub-token'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward the local Bearer token to another origin/path, even after a redirect.
        raise urllib.error.HTTPError(request.full_url, code, 'Redirect refused', headers, fp)


def opaque(value):
    if not isinstance(value, str) or not value or len(value) > 1024:
        raise ValueError('Missing or invalid event identity')
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def bounded_title(value):
    """Display metadata only, never derive a title from prompt/transcript content."""
    if not isinstance(value, str):
        return None
    value = ' '.join(''.join(c for c in value if unicodedata.category(c)[0] != 'C').split())
    value = value[:64]
    while len(value.encode('utf-8')) > 192:
        value = value[:-1]
    return value or None


def session_title(session_id, codex_home=None):
    """Best-effort local schema: exact indexed ID, read-only, bounded timeout.

    Official hooks guarantee session_id, not title. This optional local metadata
    lookup may stop working after a Codex schema update. None means unavailable
    (including a name not generated yet); an empty string means a stored empty
    name. Callers must omit unavailable metadata rather than erase a known name.
    It never selects first_user_message, preview, rollout_path or transcript text.
    """
    if not isinstance(session_id, str) or not session_id or len(session_id) > 1024:
        return None
    root = Path(codex_home) if codex_home is not None else Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    # Only known state databases, newest schema first; no recursive discovery.
    candidates = sorted(root.glob('state_[0-9]*.sqlite'), key=lambda p: int(p.stem.split('_')[-1]) if p.stem.split('_')[-1].isdigit() else -1, reverse=True)
    for path in candidates[:3]:
        try:
            with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.05)) as db:
                db.execute('PRAGMA query_only=ON')
                deadline = time.monotonic() + 0.05
                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                columns = {column[1] for column in db.execute('PRAGMA table_info(threads)')}
                # Current Desktop stores its displayed name separately; title may
                # contain the first prompt, so never fall back to it when name exists.
                field = 'name' if 'name' in columns else 'title'
                row = db.execute(f'SELECT {field} FROM threads WHERE id = ? LIMIT 1', (session_id,)).fetchone()
                if row:
                    return (bounded_title(row[0]) or '') if isinstance(row[0], str) else None
        except (sqlite3.Error, OSError, ValueError):
            continue
    return None


def input_tool_name(payload):
    # Desktop may qualify local tools with their namespace.
    return str(payload.get('tool_name', '')).rsplit('.', 1)[-1]


def question_indices(payload):
    value = payload.get('tool_input')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = None
    questions = value.get('questions') if isinstance(value, dict) else None
    # Missing input means unknown count, not "no question". -1 is a call-level
    # fallback used only on hosts which omit tool_input. Such a call cannot
    # be safely cleared by a partial reply without knowing its item count.
    return list(range(len(questions))) if isinstance(questions, list) and 0 < len(questions) <= 64 else [-1]


def answered_question_items(prompt):
    """Parse only the Desktop structured reply envelope; never retain its text.

    This is a local Desktop envelope, not a documented lifecycle-hook field.
    Ordinary free-form messages (even a new user turn) are not proof that a
    particular outstanding question has been answered.
    """
    if not isinstance(prompt, str) or len(prompt) > MAX_INPUT:
        return []
    match = re.fullmatch(r'\s*<send_user_message_question_reply>\s*(.*?)\s*</send_user_message_question_reply>\s*', prompt, re.S)
    if not match:
        return []
    try:
        replies = json.loads(match.group(1))
    except ValueError:
        return []
    if not isinstance(replies, list) or len(replies) > 64:
        return []
    result = []
    for reply in replies:
        if not isinstance(reply, dict) or 'answer' not in reply:
            continue
        item = reply.get('questionItemId')
        try:
            item = json.loads(item) if isinstance(item, str) else None
        except ValueError:
            continue
        if (not isinstance(item, list) or len(item) != 3 or
                not isinstance(item[0], str) or item[0].rsplit('.', 1)[-1] != 'request_user_input_async' or
                not isinstance(item[1], str) or not item[1] or len(item[1]) > 1024 or
                type(item[2]) is not int or not 0 <= item[2] < 64):
            continue
        result.append((opaque(item[1]), item[2]))
    return result


def tool_request_failed(payload):
    # Only explicit structured failure/cancellation is evidence that a question
    # was not posted. A normal async submission response has no answers yet.
    value = payload.get('tool_response')
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return False
    return isinstance(value, dict) and (value.get('isError') is True or
        value.get('cancelled') is True or value.get('canceled') is True or
        value.get('status') in ('error', 'failed', 'cancelled', 'canceled'))


def hook_phase(payload, current):
    """Project unresolved attention over activity, retaining only opaque IDs."""
    name = payload.get('hook_event_name')
    if name not in HOOKS:
        return None
    active = set(current.get('active', []))
    waiting = set(current.get('waiting', []))  # blocking user-input tools
    pending = dict(current.get('pending_questions', {}))  # async items by call
    raw_tool = payload.get('tool_use_id') or payload.get('tool_call_id')
    tool = opaque(raw_tool) if isinstance(raw_tool, str) and raw_tool else None
    tool_name = input_tool_name(payload)
    if name == 'SessionEnd':
        active.clear()
        waiting.clear()
        pending.clear()
        phase = 'idle'
    elif name in ('SessionStart', 'Interrupt'):
        active.clear()
        waiting.clear()
        # Resuming/interruption is not an answer to an already posted question.
        phase = 'idle'
    elif name in ('UserPromptSubmit', 'PreCompact', 'PostCompact'):
        if name == 'UserPromptSubmit':
            active.clear()
            waiting.clear()
            for call, index in answered_question_items(payload.get('prompt')):
                remaining = pending.get(call)
                if remaining is not None:
                    remaining = [i for i in remaining if i != index]
                    if remaining:
                        pending[call] = remaining
                    else:
                        pending.pop(call, None)
        phase = 'thinking'
    elif name == 'Stop':
        active.clear()
        # Finishing background work does not resolve an unanswered question.
        phase = 'completed'
    elif name == 'PermissionRequest':
        # This hook runs BEFORE routing/deciding an approval. Automatic review
        # (including a denial), another hook, or policy may handle it without
        # ever presenting a request to the user. The released hook schema has
        # no reviewer/visible-prompt evidence, and normally no tool_use_id.
        # Treat it as activity, never invent user attention from it. Genuine
        # request_user_input(_async) waits below still overlay this activity.
        # A native permission dialog needs a separate confirmed server-request
        # source; neither a timeout nor an error string can establish one.
        phase = 'running'
    elif name == 'PreToolUse':
        if tool:
            active.add(tool)
        needs_input = tool_name in (
            'request_user_input', 'request_user_input_async')
        if tool and name == 'PreToolUse' and tool_name == 'request_user_input_async':
            pending.setdefault(tool, question_indices(payload))
        elif needs_input and tool:
            waiting.add(tool)
        phase = 'running'
        if needs_input and not tool:
            phase = 'waiting'  # no correlation ID available; do not invent one
    else:
        if tool:
            active.discard(tool)
            waiting.discard(tool)
            if tool_request_failed(payload):
                pending.pop(tool, None)
        phase = 'running' if active else 'thinking'
    current['active'] = sorted(active)
    current['waiting'] = sorted(waiting)
    current['pending_questions'] = pending
    current['activity_phase'] = phase
    return 'waiting' if waiting or pending else phase


@contextmanager
def locked_record(root, session):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    path = root / (session + '.json')
    fd = os.open(root / (session + '.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            record = json.loads(path.read_text()) if path.exists() else {}
        except (ValueError, OSError):
            record = {}
        yield record
        handle, temporary = tempfile.mkstemp(prefix='.codex-', dir=root)
        try:
            with os.fdopen(handle, 'w') as output:
                json.dump(record, output, separators=(',', ':'))
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def send_event(event, token_path, endpoint):
    """Fixed loopback transport, proxies disabled, never print token or payload."""
    if endpoint != 'http://127.0.0.1:4190/v1/events':
        raise ValueError('Only the local Snowball hub endpoint is allowed')
    token = token_path.read_text().strip()
    if not token or len(token) > 512:
        raise ValueError('Invalid hub token')
    body = json.dumps(event, separators=(',', ':')).encode()
    request = urllib.request.Request(endpoint, data=body, headers={
        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=0.25) as response:
        if response.status != 200 and response.status != 202:
            raise ValueError('Hub rejected event')


def make_event(record, source, session, phase):
    record['seq'] = int(record.get('seq', 0)) + 1
    return {'version': 1, 'provider': 'Codex', 'source': source,
            'session_id': session, 'event_id': uuid.uuid4().hex,
            'seq': record['seq'], 'timestamp': time.time(), 'kind': 'task',
            'phase': phase, 'ttl_seconds': 300}


def process_hook(payload, root, emit):
    if not isinstance(payload, dict) or payload.get('hook_event_name') not in HOOKS:
        return False
    session = opaque(payload.get('session_id'))
    # Serialize reducer + delivery together; parallel tool hooks cannot reuse sequence numbers.
    with locked_record(root / 'hooks', session) as record:
        phase = hook_phase(payload, record)
        event = make_event(record, 'codex-hooks', session, phase)
        event['pending_input'] = min(4096, sum(len(items) for items in record.get('pending_questions', {}).values()))
        title = session_title(payload.get('session_id'))
        # A locked/missing database or a not-yet-generated name is not a rename.
        # Hub retains its last known title when this optional field is omitted.
        if title is not None:
            event['display_name'] = title
        # Preserve the sequence/reducer even when hub is offline; never replay stale hooks.
        try:
            emit(event)
        except (OSError, ValueError):
            pass
    return True


class JsonlReducer:
    """Only events supplied by an explicitly connected codex exec --json stream."""
    def __init__(self):
        self.session = None
        self.active = set()

    def reduce(self, event):
        if not isinstance(event, dict):
            return None
        kind = event.get('type')
        if kind == 'thread.started':
            self.session = opaque(event.get('thread_id'))
            self.active.clear()
            return 'idle'
        if not self.session:
            return None
        if kind == 'turn.started':
            self.active.clear()
            return 'thinking'
        if kind == 'turn.completed':
            self.active.clear()
            return 'completed'
        if kind == 'turn.failed':
            self.active.clear()
            return 'failed'
        # An `error` alone may be retried; do not promote it to terminal failure.
        if kind not in ('item.started', 'item.completed'):
            return None
        item = event.get('item')
        if not isinstance(item, dict):
            return None
        if item.get('type') not in ('command_execution', 'file_change', 'mcp_tool_call', 'web_search'):
            return None
        item_id = opaque(item.get('id'))
        if kind == 'item.started':
            self.active.add(item_id)
        else:
            self.active.discard(item_id)
        return 'running' if self.active else 'thinking'


def hooks_config(command):
    # Foreground localhost writes preserve event order, bounded to 250 ms on transport failure.
    return {'description': 'Snowball: local status and session display name only; no prompts or tool bodies.',
            'hooks': {event: [{'hooks': [{'type': 'command', 'command': command, 'timeout': 2}]}]
                      for event in HOOKS}}


def export_plugin(destination):
    """Create a reviewable plugin directory; never install it or alter Codex configuration."""
    destination.mkdir(parents=True, exist_ok=False)
    (destination / '.codex-plugin').mkdir()
    (destination / 'hooks').mkdir()
    (destination / 'scripts').mkdir()
    (destination / 'scripts/codex_hook.py').write_bytes(Path(__file__).read_bytes())
    manifest = {'name': 'xueqiu-status', 'version': '0.2.0',
                'description': 'Send minimal local Codex lifecycle state to the Snowball watch.',
                'hooks': './hooks/hooks.json'}
    (destination / '.codex-plugin/plugin.json').write_text(json.dumps(manifest, indent=2) + '\n')
    config = hooks_config('python3 "${PLUGIN_ROOT}/scripts/codex_hook.py" hook')
    (destination / 'hooks/hooks.json').write_text(json.dumps(config, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--token-file', type=Path, default=DEFAULT_TOKEN)
    parser.add_argument('--dry-run', action='store_true', help='Emit only sanitized event JSON; no hub writes')
    sub = parser.add_subparsers(dest='mode', required=True)
    sub.add_parser('hook', help='One official lifecycle JSON event on stdin; always fail-open')
    sub.add_parser('jsonl', help='Consume explicit codex exec --json pipe; no model invocation')
    sub.add_parser('config', help='Print standalone hooks.json snippet; does not install')
    export = sub.add_parser('export-plugin')
    export.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.mode == 'config':
        print(json.dumps(hooks_config(shlex.join([sys.executable, str(Path(__file__).resolve()), 'hook'])), indent=2))
        return 0
    if args.mode == 'export-plugin':
        export_plugin(args.destination)
        return 0
    emit = (lambda event: print(json.dumps(event), flush=True)) if args.dry_run else (
        lambda event: send_event(event, args.token_file, 'http://127.0.0.1:4190/v1/events'))
    if args.mode == 'hook':
        try:
            raw = sys.stdin.buffer.read(MAX_INPUT + 1)
            if len(raw) <= MAX_INPUT:
                process_hook(json.loads(raw), args.state_dir, emit)
        except (ValueError, OSError, TypeError, KeyError, OverflowError):
            pass
        # Stop requires JSON, other hooks accept {}; no approval/continue decision is returned.
        if not args.dry_run:
            print('{}')
        return 0
    reducer = JsonlReducer()
    while True:
        raw = sys.stdin.buffer.readline(MAX_INPUT + 1)
        if not raw:
            break
        if len(raw) > MAX_INPUT:
            # Drain one oversized item without buffering its contents or treating fragments as events.
            while raw and not raw.endswith(b'\n'):
                raw = sys.stdin.buffer.readline(MAX_INPUT + 1)
            continue
        try:
            phase = reducer.reduce(json.loads(raw))
            if phase:
                with locked_record(args.state_dir / 'jsonl', reducer.session) as record:
                    event = make_event(record, 'codex-cli', reducer.session, phase)
                    try:
                        emit(event)
                    except (OSError, ValueError):
                        pass
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    # EOF is not success or failure: a pipe may close due to cancellation or a crashed producer.
    return 0


if __name__ == '__main__':
    sys.exit(main())
