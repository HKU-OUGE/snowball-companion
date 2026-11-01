#!/usr/bin/env python3
"""Manage this install's status hub, using its user service when installed."""
import argparse
from platform_support import flock, LOCK_EX, private_file_ok
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

from status_hub import STATE_DIR, TOKEN_PATH, atomic_json, private_dir

HUB = Path(__file__).resolve().with_name('status_hub.py')
RUNTIME_DIR = Path.home() / '.local/state/xueqiu'


def identity(pid):
    """Return Linux process start tick only when argv contains our exact script path."""
    if type(pid) is not int or pid <= 1:
        return None
    process = Path('/proc') / str(pid)
    try:
        argv = (process / 'cmdline').read_bytes().split(b'\0')
        if str(HUB).encode() not in argv:
            return None
        # /proc comm can contain parentheses and spaces; split at its last closing ')'.
        fields = (process / 'stat').read_text().rsplit(') ', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return fields[19]
    except (OSError, ValueError, IndexError):
        return None


def read_record(path):
    try:
        record = json.loads(path.read_text())
        if not isinstance(record, dict) or record.get('script') != str(HUB):
            return None
        if identity(record.get('pid')) != record.get('start') or record.get('start') is None:
            return None
        if type(record.get('port')) is not int or not 1 <= record['port'] <= 65535:
            return None
        if not isinstance(record.get('token_file'), str) or not isinstance(record.get('state_dir'), str):
            return None
        return record
    except (OSError, ValueError):
        return None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def health(record):
    """Authenticated local health only, no proxies/redirects and no token output."""
    try:
        path = Path(record['token_file'])
        if not private_file_ok(path):
            return False
        token = path.read_text().strip()
        if not 32 <= len(token) <= 128:
            return False
        request = urllib.request.Request(f'http://127.0.0.1:{record["port"]}/v1/health',
                                         headers={'Authorization': 'Bearer ' + token})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=.4) as response:
            raw = response.read(1025)
            if response.status != 200 or len(raw) > 1024:
                return False
            payload = json.loads(raw)
        return (isinstance(payload, dict) and payload.get('ok') is True
                and payload.get('version') == 1 and payload.get('hub_alive') is True)
    except (OSError, ValueError, KeyError, urllib.error.URLError):
        return False


def is_same(record):
    return record.get('script') == str(HUB) and identity(record.get('pid')) == record.get('start')


def terminate_managed(record, timeout=5):
    if not is_same(record):
        return True
    try:
        os.kill(record['pid'], signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_same(record):
            return True
        time.sleep(.05)
    return not is_same(record)


def available_port(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', port))
            return True
        except OSError:
            return False


def run(args):
    if sys.platform=='win32':
        import windows_runtime
        if (args.runtime_dir.expanduser().resolve()!=RUNTIME_DIR or
                args.state_dir.expanduser().resolve()!=STATE_DIR or
                args.token_file.expanduser().resolve()!=TOKEN_PATH or args.port!=4190):
            raise ValueError('Windows shared runtime requires the fixed local Hub settings')
        result=getattr(windows_runtime,args.command)()
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('state') not in ('unverified','unreachable') else 2
    from user_services import owned,control
    if (owned('hub') and args.runtime_dir.expanduser().resolve()==RUNTIME_DIR and
            args.state_dir.expanduser().resolve()==STATE_DIR and args.token_file.expanduser().resolve()==TOKEN_PATH and args.port==4190):
        code=control('hub',args.command)
        if args.command=='status' and code==0 and not health({'token_file':str(TOKEN_PATH),'port':4190}):
            print('User service is active but authenticated Hub health failed.');return 2
        return code
    runtime_dir = private_dir(args.runtime_dir.expanduser().resolve())
    record_path, log_path = runtime_dir / 'hub-runtime.json', runtime_dir / 'hub.log'
    lock_fd = os.open(runtime_dir / 'hub-manager.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flock(lock_fd, LOCK_EX)
        record = read_record(record_path)
        if args.command == 'status':
            if record is None:
                print('Stopped: no matching managed Snowball hub process.')
                return 1
            if not health(record):
                print('Running but unhealthy: managed process exists; authenticated HTTP health failed.')
                print(f'Private log: {log_path}')
                return 2
            print(f'Running and healthy: 127.0.0.1:{record["port"]}')
            print(f'Snapshot: {Path(record["state_dir"]) / "snapshot.json"}')
            print('This confirms the hub only, not AI source freshness or Bluetooth connectivity.')
            return 0
        if args.command == 'stop':
            if record is None:
                print('No matching managed hub to stop; no process was signaled.')
                return 0
            if not terminate_managed(record):
                print('Stop not confirmed: managed hub did not exit after SIGTERM; record retained.', file=sys.stderr)
                return 2
            record_path.unlink(missing_ok=True)
            print('Stopped this managed Snowball hub.')
            return 0
        if record is not None:
            target = {'state_dir': str(args.state_dir.expanduser().resolve()),
                      'token_file': str(args.token_file.expanduser().resolve()), 'port': args.port}
            if any(record[key] != value for key, value in target.items()):
                print('A managed hub is already running with different settings; stop it before changing settings.', file=sys.stderr)
                return 2
            if health(record):
                print(f'Snowball hub already running and healthy on 127.0.0.1:{record["port"]}.')
                return 0
            print('Managed hub process exists but is unhealthy; inspect its private log and stop it before restarting.', file=sys.stderr)
            return 2
        if not available_port(args.port):
            print(f'Cannot start: 127.0.0.1:{args.port} is already in use. No existing process was stopped.', file=sys.stderr)
            return 2
        state_dir = args.state_dir.expanduser().resolve()
        token_file = args.token_file.expanduser().resolve()
        log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.fchmod(log_fd, 0o600)
        with os.fdopen(log_fd, 'a') as log:
            process = subprocess.Popen([sys.executable, str(HUB), '--port', str(args.port),
                '--state-dir', str(state_dir), '--token-file', str(token_file)],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
                cwd=HUB.parent)
        record = {'pid': process.pid, 'script': str(HUB), 'start': None,
                  'port': args.port, 'state_dir': str(state_dir), 'token_file': str(token_file)}
        deadline = time.monotonic() + args.start_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                print(f'Hub failed to start (exit {process.returncode}). Private log: {log_path}', file=sys.stderr)
                return 2
            record['start'] = identity(process.pid)
            if record['start'] is not None and health(record):
                # Allow a child bind error to surface before mistaking another server's health for ours.
                time.sleep(.1)
                if process.poll() is None and is_same(record):
                    atomic_json(record_path, record)
                    print(f'Background Snowball hub started and authenticated: 127.0.0.1:{args.port}')
                    print(f'Snapshot: {state_dir / "snapshot.json"}')
                    print('Local process only; no boot autostart installed. BLE bridge is managed separately.')
                    return 0
            time.sleep(.05)
        if record['start'] is not None and not terminate_managed(record):
            atomic_json(record_path, record)
            print(f'Hub startup timed out; cleanup not confirmed. Record retained for stop. Private log: {log_path}', file=sys.stderr)
        else:
            process.wait(timeout=2)
            print(f'Hub startup timed out; child stopped. Private log: {log_path}', file=sys.stderr)
        return 2
    finally:
        os.close(lock_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('start', 'stop', 'status'))
    parser.add_argument('--port', type=int, default=4190)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--token-file', type=Path, default=TOKEN_PATH)
    parser.add_argument('--runtime-dir', type=Path, default=RUNTIME_DIR,
                        help='Private manager log/record directory; use overrides for isolated tests')
    parser.add_argument('--start-timeout', type=float, default=8)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not .5 <= args.start_timeout <= 60:
        parser.error('port must be 1..65535 and startup timeout .5..60 seconds')
    try:
        return run(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        # Paths and HTTP input can contain sensitive material; avoid dumping exception text.
        print('Hub management failed. Check private directory access and configured settings.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
