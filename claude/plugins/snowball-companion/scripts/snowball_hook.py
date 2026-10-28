#!/usr/bin/env python3
"""Observational Claude hooks. No permission decisions, transcript access or replay."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unicodedata
import urllib.request
import uuid

SOURCE = 'claude-code-plugin'
EVENTS = ('SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PostToolUse',
          'PostToolUseFailure', 'Notification', 'Stop', 'StopFailure',
          'SessionEnd', 'PreCompact', 'PostCompact')
INPUT_LIMIT = 1024 * 1024
HUB = 'http://127.0.0.1:4190'
TTL = 300


def clean(value, chars=256, byte_limit=768):
    if not isinstance(value, str):
        return ''
    value = ' '.join(''.join(c for c in value if unicodedata.category(c)[0] != 'C' or c in '\n\t').split())
    result = value[:chars]
    while len(result.encode('utf-8')) > byte_limit:
        result = result[:-1]
    if result != value:
        result = result[:-1] + '…'
    return result


def opaque(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def identity(payload):
    value = payload.get('session_id')
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError('invalid_session')
    return opaque(SOURCE + '\0' + value)


def initial_title(payload, sid):
    # Hook fields do not guarantee the Desktop sidebar title. Never read the
    # transcript or derive a title from the user's prompt or tool arguments.
    project = clean(Path(payload.get('cwd') or '.').name, 28, 84)
    return clean('Claude · ' + (project or '本机会话') + ' · ' + sid[:6], 64, 192)


def transition(old, event, payload):
    if event not in EVENTS or payload.get('hook_event_name') != event or payload.get('agent_id'):
        return None
    sid = identity(payload)
    s = dict(old)
    pending = dict(s.get('pending', {}))
    phase = s.get('phase', 'idle')
    call = payload.get('tool_use_id')
    call = opaque(call) if isinstance(call, str) and call else 'unknown-tool'
    tool = payload.get('tool_name')
    if event in ('SessionStart', 'SessionEnd', 'UserPromptSubmit'):
        pending = {}
        phase = 'thinking' if event == 'UserPromptSubmit' else 'idle'
    elif event == 'PreToolUse':
        # A later actual tool starts only after the preceding permission flow.
        pending.pop('permission', None)
        phase = 'running'
        if tool == 'AskUserQuestion':
            args = payload.get('tool_input')
            args = args if isinstance(args, dict) else {}
            questions = args.get('questions', [])
            answers = args.get('answers')
            answers = answers if isinstance(answers, dict) else {}
            rows, valid_count = [], 0
            if isinstance(questions, list):
                for i, q in enumerate(questions[:8]):
                    if isinstance(q, dict):
                        text = clean(q.get('question'))
                        if text:
                            valid_count += 1
                            answered = answers.get(q['question'])
                            if not (isinstance(answered, str) and answered.strip()):
                                rows.append({'id': opaque(sid + call + str(i)), 'text': text})
            if rows or not valid_count:
                pending[call] = rows or [{'id': opaque(sid + call), 'text': 'Claude 有问题需要你回答，请回电脑查看'}]
            else:
                pending.pop(call, None)
    elif event in ('PostToolUse', 'PostToolUseFailure'):
        pending.pop(call, None)
        pending.pop('permission', None)
        phase = 'thinking'  # A failed tool is not a failed turn.
    elif event in ('PreCompact', 'PostCompact'):
        phase = 'thinking'
    elif event == 'Notification':
        notice = payload.get('notification_type')
        if notice == 'permission_prompt':
            # Unlike PermissionRequest, this documents an actual displayed
            # prompt. Auto-approved tools never pass through this branch.
            text = 'Claude 正在请求操作权限，请回电脑确认'
            pending['permission'] = [{'id': opaque(sid + 'permission' + str(s.get('turn', 0))), 'text': text}]
        elif notice in ('elicitation_dialog', 'elicitation_url_dialog'):
            text = clean(payload.get('message')) or 'Claude 需要补充信息，请回电脑查看'
            pending['elicitation'] = [{'id': opaque(sid + 'elicitation' + text), 'text': text}]
        elif notice in ('elicitation_response', 'elicitation_complete'):
            pending.pop('elicitation', None)
            phase = 'thinking'
        elif notice == 'idle_prompt':
            # Do not erase unresolved questions or reclassify terminal failure.
            if phase not in ('failed', 'completed'):
                phase = 'idle'
        else:
            return None
    elif event in ('Stop', 'StopFailure'):
        if event == 'StopFailure':
            pending = {}
            phase = 'failed'
        else:
            # A terminal response closes notification-only blocking dialogs,
            # including a declined permission which produced no tool result.
            # Explicit question IDs still need their matching resolution.
            pending.pop('permission', None)
            pending.pop('elicitation', None)
            if not pending:
                phase = 'completed'  # End of this response, not project success.
    if event == 'UserPromptSubmit':
        s['turn'] = s.get('turn', 0) + 1
    s.update(version=1, phase=phase, pending=pending,
             display_name=s.get('display_name') or initial_title(payload, sid))
    return s


def root_dir():
    return Path.home() / '.config' / 'xueqiu'


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('untrusted_directory')


def private_open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        raise ValueError('untrusted_file')
    return fd


def read_token(root):
    with os.fdopen(private_open(root / 'hub-token', os.O_RDONLY)) as f:
        token = f.read(4097).strip()
    if not 32 <= len(token) <= 128 or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in token):
        raise ValueError('invalid_token')
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request(path, token, data=None, timeout=.35):
    if path not in ('/v1/events', '/v1/health', '/v1/pet'):
        raise ValueError('unsupported_endpoint')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(HUB + path, data=None if data is None else json.dumps(data, separators=(',', ':')).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='GET' if data is None else 'POST')
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(4097)
        if response.status != 200 or len(raw) > 4096:
            raise ValueError('invalid_response')
        return json.loads(raw)


@contextmanager
def locked(folder, sid):
    private_dir(folder.parent)
    private_dir(folder)
    fd = private_open(folder / (sid + '.lock'), os.O_RDWR | os.O_CREAT)
    try:
        until = time.monotonic() + .2
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > until:
                    raise TimeoutError('hook_busy')
                time.sleep(.01)
        yield
    finally:
        os.close(fd)


def save(path, state):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, ensure_ascii=True, separators=(',', ':'))
            f.flush()
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def process(event, payload, *, root=None, sender=None):
    if not isinstance(payload, dict) or event not in EVENTS or payload.get('hook_event_name') != event or payload.get('agent_id'):
        return None
    sid = identity(payload)
    root = root or root_dir()
    token = read_token(root)
    folder = root / 'hook-state' / SOURCE
    with locked(folder, sid):
        path = folder / (sid + '.json')
        try:
            with os.fdopen(private_open(path, os.O_RDONLY)) as f:
                old = json.loads(f.read(65537))
        except FileNotFoundError:
            old = {}
        new = transition(old, event, payload)
        if new is None:
            return None
        seq = old.get('seq', 0) + 1
        if type(seq) is not int or not 1 <= seq < 2**53:
            raise ValueError('invalid_sequence')
        new.update(seq=seq, observed_at=time.time())
        save(path, new)  # Reserve sequence even if delivery fails; never replay.
        questions = [q for rows in new['pending'].values() for q in rows][:64]
        outgoing = {'version': 1, 'provider': 'Claude', 'source': SOURCE, 'session_id': sid,
            'event_id': str(uuid.uuid4()), 'seq': seq, 'timestamp': new['observed_at'],
            'kind': 'task', 'phase': 'waiting' if questions else new['phase'],
            'activity_phase': new['phase'], 'ttl_seconds': TTL, 'display_name': new['display_name'],
            'pending_input': len(questions), 'questions': questions}
        result = sender(outgoing, token) if sender else request('/v1/events', token, outgoing)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError('hub_rejected')
        return outgoing


def main(argv=None):
    try:
        args = sys.argv[1:] if argv is None else argv
        if len(args) != 1 or args[0] not in EVENTS:
            return 0
        raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        if len(raw) > INPUT_LIMIT:
            return 0
        process(args[0], json.loads(raw))
    except Exception:
        pass  # Never print payloads/errors, block work, approve, or change input.
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
