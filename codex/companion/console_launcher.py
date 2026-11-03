"""Open the bundled console without a development server or a memorized port."""
import argparse
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import webbrowser

from private_files import private_dir, private_file, private_write, read_text

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / '.local/state/xueqiu/console'
INSTALL = Path.home() / '.config/xueqiu/console-installation.json'


def active_url(state=STATE):
    connection = None
    try:
        record = json.loads(read_text(state / 'server.json'))
        port = record['port']
        if type(port) is not int or not 1024 <= port <= 65535 or record.get('protocol') != 1:
            return None
        token = record['token']
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            return None
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=1)
        connection.request('GET', '/health', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        body = response.read(2049)
        if response.status != 200 or len(body) > 2048:
            return None
        value = json.loads(body)
        if value.get('app') == 'snowball-console' and value.get('protocol') == 1:
            return f'http://127.0.0.1:{port}/'
    except (OSError, ValueError, KeyError, http.client.HTTPException):
        return None
    finally:
        if connection:
            connection.close()


def register(root=ROOT, python=None):
    root = Path(root).resolve()
    if not (root / 'console_server.py').is_file() or not (root / 'console_assets/index.html').is_file():
        raise RuntimeError('Bundled console files are missing. Extract the entire package first.')
    private_write(INSTALL, json.dumps({'schema': 1, 'runtime': str(root),
                                      'python': str(Path(python or sys.executable).resolve())}).encode())


def open_console(browser=True, root=ROOT, setup=False, adapter='codex'):
    url = active_url()
    if setup and url:
        from console_upgrade import ensure_setup_console
        ensure_setup_console(STATE, root)
        url = active_url()
    if not url:
        from console_backend import interpreter
        private_dir(STATE)
        log_path = STATE / 'server.log'
        with log_path.open('ab', buffering=0) as log:
            private_file(log_path)
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
            process = subprocess.Popen([interpreter(root), '-X', 'utf8', str(root / 'console_server.py')],
                                       cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, **options)
        until = time.monotonic() + 15
        while time.monotonic() < until:
            url = active_url()
            if url:
                break
            if process.poll() not in (None, 0):
                raise RuntimeError('Console startup failed. See the private console/server.log.')
            time.sleep(.2)
        if not url:
            raise RuntimeError('Console did not become ready. Reopen it after checking the local runtime.')
    # A one-use, 60-second ticket in the fragment authenticates the browser.
    # The persistent private bearer token, pairing key and Wi-Fi password never
    # enter a URL. The page removes the fragment before requesting device data.
    record = json.loads(read_text(STATE / 'server.json'))
    connection = http.client.HTTPConnection('127.0.0.1', record['port'], timeout=3)
    try:
        connection.request('POST', '/launch', b'{}',
                           {'Authorization': 'Bearer ' + record['token'], 'Content-Type': 'application/json'})
        response = connection.getresponse()
        data = response.read(1025)
        if response.status != 200 or len(data) > 1024:
            raise RuntimeError('Console browser authorization failed.')
        ticket = json.loads(data)['ticket']
        if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
            raise RuntimeError('Invalid console launch response.')
        url += '#setup=' + ticket
    finally:
        connection.close()
    if browser:
        webbrowser.open(url, new=2)
    if setup:
        from setup_agent import begin
        begin(adapter)
    return url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['open', 'register'], default='open')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--setup', action='store_true')
    parser.add_argument('--adapter', choices=('codex', 'claude', 'cursor', 'device'), default='codex')
    args = parser.parse_args()
    if os.name == 'nt':
        from windows_app_endpoint import remember_current_endpoint
        remember_current_endpoint()
    if args.command == 'register':
        register()
        print('Snowball console entry registered.')
    else:
        register()
        url = open_console(not args.no_browser, setup=args.setup, adapter=args.adapter)
        print(url if args.no_browser else url.split('#', 1)[0])


if __name__ == '__main__':
    main()
