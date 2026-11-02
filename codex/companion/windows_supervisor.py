"""Run the current user's real Hub and BLE bridge on Windows.

Use windows-services.ps1 to register a login task. Does not pair, create demo
states, approve Hooks, send chats, or install device firmware.
"""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from private_files import private_dir, private_file

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / '.local/state/xueqiu/windows'
CONFIG = Path.home() / '.config/xueqiu'
TASK_NAME = 'Snowball Companion Auto Reconnect'


def hub_healthy(config=CONFIG):
    connection = http.client.HTTPConnection('127.0.0.1', 4190, timeout=2)
    try:
        token = (config / 'hub-token').read_text(encoding='utf-8').strip()
        connection.request('GET', '/v1/health', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        raw = response.read(1025)
        if response.status != 200 or len(raw) > 1024:
            return False
        value = json.loads(raw)
        return value.get('ok') is True and value.get('version') == 1 and value.get('hub_alive') is True
    except (OSError, ValueError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def child_arguments(name, state=None):
    if name == 'status_hub':
        return []
    if name == 'bridge':
        state = state or Path.home() / '.local/state/xueqiu/hub'
        return ['watch', '--source', str(state / 'snapshot.json')]
    raise ValueError('Unknown managed process')


def main():
    if sys.platform != 'win32':
        raise SystemExit('Use user_services.py on Linux; this supervisor is for Windows.')
    from windows_file_lock import flock, LOCK_EX, LOCK_NB
    private_dir(STATE)
    lock = open(STATE / 'supervisor.lock', 'a+b')
    try:
        flock(lock, LOCK_EX | LOCK_NB)
    except BlockingIOError:
        lock.close()
        return
    children, logs = {}, {}
    def launch(name):
        if name in logs:
            logs[name].close()
        path = STATE / (name + '.log')
        if path.exists() and path.stat().st_size > 5_000_000:
            os.replace(path, path.with_suffix('.previous.log'))
        logs[name] = open(path, 'ab', buffering=0)
        private_file(path)
        children[name] = subprocess.Popen(
            [sys.executable, '-X', 'utf8', str(ROOT / (name + '.py')), *child_arguments(name)],
            cwd=ROOT, stdout=logs[name], stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        while True:
            # Adopt an authenticated, already-running Hub instead of duplicating it.
            if not hub_healthy() and ('status_hub' not in children or children['status_hub'].poll() is not None):
                launch('status_hub')
            if (CONFIG / 'ble-pairing.json').is_file() and ('bridge' not in children or children['bridge'].poll() is not None):
                launch('bridge')
            time.sleep(10)
    finally:
        for child in children.values():
            if child.poll() is None:
                child.terminate()
        for log in logs.values():
            log.close()
        lock.close()


if __name__ == '__main__':
    main()
