"""Question metadata and reminder policy. Never answers or approves a request."""
import re
import unicodedata

QUESTION_MAX = 64
TEXT_BYTES = 768
TEXT_CHARS = 256
SNOOZE_SECONDS = 600
QID = re.compile(r'^[0-9a-f]{32}$')


def clean_question(value):
    if not isinstance(value, str):
        return '问题详情未提供，请回电脑查看'
    text = ' '.join(''.join(c for c in value if unicodedata.category(c)[0] != 'C' or c in '\n\t').split())
    clipped = text[:TEXT_CHARS]
    while len(clipped.encode()) > TEXT_BYTES:
        clipped = clipped[:-1]
    if clipped != text:
        clipped = clipped[:-1] + '…'
    return clipped or '问题详情未提供，请回电脑查看'


def valid_questions(value):
    if not isinstance(value, list) or len(value) > QUESTION_MAX:
        return False
    ids = set()
    for q in value:
        if (not isinstance(q, dict) or not {'id', 'text'} <= set(q) or set(q) - {'id', 'text', 'options'} or
                not isinstance(q['id'], str) or not QID.fullmatch(q['id']) or q['id'] in ids or
                not isinstance(q['text'], str) or q['text'] != clean_question(q['text'])):
            return False
        options = q.get('options', [])
        if (not isinstance(options, list) or len(options) > 6 or
                any(not isinstance(o, str) or not o or len(o)>80 or len(o.encode())>240 or o != clean_question(o) for o in options) or
                len(set(options)) != len(options)):
            return False
        ids.add(q['id'])
    return True


def question_rows(record, now):
    result = []
    for q in record.get('questions', []):
        setting = record.get('reminders', {}).get(q['id'], {})
        mode, until = setting.get('mode'), setting.get('until', 0)
        status = 'ignored' if mode == 'ignore' else 'snoozed' if mode == 'snooze' and until > now else 'active'
        result.append({**q, 'status': status, 'until': until if status == 'snoozed' else 0})
    return result


def projection(record, now):
    rows = question_rows(record, now)
    total = record.get('pending_input', 0)
    pending = max(0, total - len(rows)) + sum(q['status'] == 'active' for q in rows)
    phase = record.get('phase')
    if pending:
        phase = 'waiting'
    elif total and phase == 'waiting':
        fresh = min(record.get('expires_at', 0), record.get('task_expires_at') or 0) > now
        activity = record.get('activity_phase', 'idle')
        phase = activity if fresh and activity != 'waiting' else 'idle'
    return phase, pending, total, rows
