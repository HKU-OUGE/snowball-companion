#!/usr/bin/env python3
"""Observe Cursor lifecycle hooks without issuing permissions or reading transcripts."""
import json
import os
from pathlib import Path
import sys
import time
import uuid
from shared import clean, locked, opaque, private_open, read_token, request, root_dir, save

SOURCE = 'cursor-agent-plugin'
EVENTS = frozenset(('sessionStart', 'afterAgentThought', 'afterAgentResponse',
                    'postToolUse', 'postToolUseFailure', 'preCompact', 'stop', 'sessionEnd'))
ACTIVE = frozenset(('afterAgentThought', 'afterAgentResponse', 'postToolUse',
                    'postToolUseFailure', 'preCompact'))
INPUT_LIMIT = 1024 * 1024
TTL = 300


def identity(payload):
    conversation = payload.get('conversation_id') or payload.get('session_id')
    if not isinstance(conversation, str) or not 1 <= len(conversation) <= 512:
        raise ValueError('invalid_conversation')
    return opaque(SOURCE + '\0' + conversation)


def generation(payload):
    value = payload.get('generation_id')
    if value is None or value == '':
        return ''
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError('invalid_generation')
    return opaque(SOURCE + '\0generation\0' + value)


def title(payload, sid):
    roots = payload.get('workspace_roots')
    project = ''
    if isinstance(roots, list) and roots and isinstance(roots[0], str):
        project = clean(Path(roots[0]).name, 28, 84)
    return clean('Cursor · ' + (project or '本机会话') + ' · ' + sid[:6], 64, 192)


def transition(old, event, payload):
    if event not in EVENTS or payload.get('hook_event_name') != event:
        return None
    # A local plugin is not a feed from cloud agents or subagents. In
    # particular, their completion must never finish the parent conversation.
    if payload.get('is_background_agent') is True or payload.get('subagent_id') or payload.get('parent_conversation_id'):
        return None
    sid = identity(payload)
    gen = generation(payload)
    if event in ACTIVE | {'stop'} and not gen:
        # Without the documented per-turn ID, a late callback cannot be
        # distinguished from a later turn. Do not synthesize activity.
        return None
    prior = old.get('generation', '')
    retired = list(old.get('retired_generations', []))[-16:]
    if gen and gen in retired:
        return None
    if event == 'sessionStart' and old.get('seq'):
        # Cursor documents sessionStart as fire-and-forget; it can arrive late.
        return None
    if event in ACTIVE or event == 'stop':
        if gen and gen != prior:
            if prior:
                retired = (retired + [prior])[-16:]
            prior = gen
        elif gen and old.get('terminal'):
            # Late callbacks from a finished generation cannot revive it.
            return None
    state = dict(old)
    phase = old.get('phase', 'idle')
    terminal = bool(old.get('terminal', False))
    if event == 'sessionStart':
        phase, terminal = 'idle', False
    elif event in ACTIVE:
        phase, terminal = 'thinking', False
    elif event == 'stop':
        status = payload.get('status')
        if status not in ('completed', 'aborted', 'error'):
            return None
        phase = {'completed': 'completed', 'aborted': 'idle', 'error': 'failed'}[status]
        terminal = True
    elif event == 'sessionEnd':
        reason = payload.get('reason')
        if reason not in ('completed', 'aborted', 'error', 'window_close', 'user_close'):
            return None
        # Closing a session is not itself a completed turn.
        phase = 'failed' if reason == 'error' else 'idle'
        terminal = True
    state.update(version=1, generation=prior, retired_generations=retired,
                 phase=phase, terminal=terminal,
                 display_name=old.get('display_name') or title(payload, sid))
    return state


def process(event, payload, *, root=None, sender=None):
    if not isinstance(payload, dict) or event not in EVENTS or payload.get('hook_event_name') != event:
        return None
    if payload.get('is_background_agent') is True or payload.get('subagent_id') or payload.get('parent_conversation_id'):
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
        save(path, new)  # A failed delivery is never queued for replay.
        outgoing = {'version': 1, 'provider': 'Cursor', 'source': SOURCE,
                    'session_id': sid, 'event_id': str(uuid.uuid4()), 'seq': seq,
                    'timestamp': new['observed_at'], 'kind': 'task',
                    'phase': new['phase'], 'ttl_seconds': TTL,
                    'display_name': new['display_name'], 'pending_input': 0,
                    'questions': []}
        result = sender(outgoing, token) if sender else request('/v1/events', token, outgoing)
        if not isinstance(result, dict) or result.get('ok') is not True:
            raise ValueError('hub_rejected')
        return outgoing


def main():
    # These are observation-only hooks; an empty JSON object never requests a
    # follow-up, modifies tool output, or decides permissions.
    try:
        if len(sys.argv) == 2 and sys.argv[1] in EVENTS:
            raw = sys.stdin.buffer.read(INPUT_LIMIT + 1)
            if len(raw) <= INPUT_LIMIT:
                payload = json.loads(raw)
                process(sys.argv[1], payload)
    except Exception:
        pass  # Missing hub or bad input must not disrupt Cursor work.
    print('{}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
