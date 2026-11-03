"""Private handoff of the host-injected Windows endpoint to background services.

Only a local endpoint name is stored, never tokens, environment dumps or chat
content. Consumers still verify the connected server before sending any bytes.
"""
import json
import os
from pathlib import Path
import re
import sys

from private_files import private_write, safe_reader

VARIABLE = 'CODEX_APP_TOOLS_PIPE_PATH'
PATTERN = re.compile(r'\\\\\.\\pipe\\codex-app-tools-[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}')


def valid(value):
    return isinstance(value, str) and PATTERN.fullmatch(value) is not None


def receipt_path():
    return Path.home()/'.config/xueqiu/codex-app-endpoint.json'


def stored_endpoint():
    try:
        with safe_reader(receipt_path()) as stream:
            raw = stream.read(513)
        if len(raw) > 512:
            return None
        value = json.loads(raw)
        if isinstance(value, dict) and set(value) == {'schema', 'pipe'} and value['schema'] == 1 and valid(value['pipe']):
            return value['pipe']
    except (OSError, ValueError, TypeError):
        pass
    return None


def remember_current_endpoint():
    """Called inside Codex's hook/setup environment, not by a scheduled task."""
    if sys.platform != 'win32':
        return False
    value = os.environ.get(VARIABLE, '')
    if not valid(value):
        return False
    if stored_endpoint() != value:
        private_write(receipt_path(), (json.dumps({'schema': 1, 'pipe': value})+'\n').encode())
    return True


def candidates():
    if sys.platform != 'win32':
        return ()
    inherited = os.environ.get(VARIABLE, '')
    # An explicitly malformed host value must not silently select an old host.
    if inherited and not valid(inherited):
        return ()
    saved = stored_endpoint()
    return tuple(dict.fromkeys(x for x in (inherited, saved) if valid(x)))
