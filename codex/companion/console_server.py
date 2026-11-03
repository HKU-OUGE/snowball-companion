"""Packaged loopback control panel. Static assets, scoped APIs, no shell endpoint."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
from pathlib import Path
import secrets
import sys
import threading
import time

from console_backend import Backend, SetupError, ROOT, STATE
from private_files import private_dir, private_file, private_write

VERSION = '0.5.4'
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'),
          '/console.css': ('console.css', 'text/css; charset=utf-8'),
          '/console.js': ('console.js', 'text/javascript; charset=utf-8'),
          '/snowball.png': ('snowball.png', 'image/png'),
          '/favicon.ico': ('snowball.png', 'image/png')}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 12

    def __init__(self, port, backend, token, assets=None):
        self.backend, self.token = backend, token
        self.assets = Path(assets or ROOT / 'console_assets')
        self.sessions = {}
        self.tickets = {}
        self.sessions_lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(12)
        super().__init__(('127.0.0.1', port), Handler)

    @property
    def origin(self):
        return 'http://127.0.0.1:' + str(self.server_port)

    def process_request(self, request, address):
        if not self.slots.acquire(False):
            self.shutdown_request(request)
            return
        request.settimeout(5)
        super().process_request(request, address)

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def session(self, existing=None):
        now = time.monotonic()
        with self.sessions_lock:
            self.sessions = {key: at for key, at in self.sessions.items() if now - at < 3600}
            if existing in self.sessions:
                self.sessions[existing] = now
                return existing
            if len(self.sessions) >= 64:
                del self.sessions[min(self.sessions, key=self.sessions.get)]
            key = secrets.token_urlsafe(32)
            self.sessions[key] = now
            return key

    def issue_ticket(self):
        now = time.monotonic()
        with self.sessions_lock:
            self.tickets = {k: at for k, at in self.tickets.items() if now - at < 60}
            if len(self.tickets) >= 64:
                del self.tickets[min(self.tickets, key=self.tickets.get)]
            key = secrets.token_urlsafe(32)
            self.tickets[key] = now
            return key

    def consume_ticket(self, ticket):
        if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
            raise SetupError('browser_session_required')
        with self.sessions_lock:
            at = self.tickets.pop(ticket, 0)
        if not at or not 0 <= time.monotonic() - at < 60:
            raise SetupError('browser_session_required')
        return self.session()


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'SnowballConsole/1'

    def log_message(self, *_):
        pass  # Never write passwords, request bodies, or browser sessions to logs.

    def reply(self, code, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def local(self):
        hosts = self.headers.get_all('Host', [])
        if self.client_address[0] != '127.0.0.1' or hosts != [self.server.origin[7:]]:
            raise SetupError('local_only')
        origins = self.headers.get_all('Origin', [])
        if origins and origins != [self.server.origin]:
            raise SetupError('local_only')
        if self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
            raise SetupError('local_only')

    def browser_auth(self):
        self.local()
        if self.headers.get_all('X-Snowball-UI', []) != ['1']:
            raise SetupError('browser_session_required')
        sessions = self.headers.get_all('X-Snowball-Session', [])
        if len(sessions) != 1 or not 32 <= len(sessions[0]) <= 128:
            raise SetupError('browser_session_required')
        session = sessions[0]
        with self.server.sessions_lock:
            at = self.server.sessions.get(session, 0)
            if not at or time.monotonic() - at >= 3600:
                raise SetupError('browser_session_required')
            self.server.sessions[session] = time.monotonic()

    def launcher_auth(self):
        self.local()
        authorization = self.headers.get_all('Authorization', [])
        if len(authorization) != 1 or not hmac.compare_digest(authorization[0], 'Bearer ' + self.server.token):
            raise SetupError('local_only')

    def do_GET(self):
        try:
            self.local()
            if self.path == '/health':
                self.launcher_auth()
                return self.reply(200, {'app': 'snowball-console', 'protocol': 1, 'version': VERSION})
            if self.path in ASSETS:
                name, content_type = ASSETS[self.path]
                return self.reply(200, (self.server.assets / name).read_bytes(), content_type)
            self.browser_auth()
            if self.path == '/api/status':
                return self.reply(200, dict(self.server.backend.snapshot(), version=VERSION))
            if self.path == '/api/ports':
                return self.reply(200, {'ports': self.server.backend.ports()})
            return self.reply(404, {'error': 'not_found'})
        except SetupError as error:
            self.reply(403, {'error': str(error)})
        except (OSError, ValueError):
            self.reply(503, {'error': 'local_service_unavailable'})

    def do_POST(self):
        try:
            if self.path == '/launch':
                self.launcher_auth()
            elif self.path == '/api/bootstrap':
                self.local()
                if self.headers.get_all('X-Snowball-UI', []) != ['1']:
                    raise SetupError('browser_session_required')
            else:
                self.browser_auth()
            lengths = self.headers.get_all('Content-Length', [])
            if (self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit() or
                    not 2 <= int(lengths[0]) <= 4096 or
                    self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                raise SetupError('invalid_request')
            raw = self.rfile.read(int(lengths[0]))
            if len(raw) != int(lengths[0]):
                raise SetupError('invalid_request')
            def pairs(values):
                result = {}
                for key, value in values:
                    if key in result:
                        raise SetupError('invalid_request')
                    result[key] = value
                return result
            data = json.loads(raw, object_pairs_hook=pairs)
            if not isinstance(data, dict):
                raise SetupError('invalid_request')
            if self.path == '/launch':
                if data:
                    raise SetupError('invalid_request')
                return self.reply(200, {'ticket': self.server.issue_ticket()})
            if self.path == '/api/bootstrap':
                if set(data) != {'ticket'}:
                    raise SetupError('invalid_request')
                # An origin-scoped tab token avoids sharing a loopback cookie
                # with unrelated services on other ports of the same host.
                return self.reply(200, {'ok': True, 'session': self.server.consume_ticket(data['ticket'])})
            if self.path == '/api/provision':
                return self.reply(200, self.server.backend.provision(data))
            if self.path == '/api/action':
                action = data.pop('action', None)
                schemas = {'prepare': set(), 'services': set(), 'bind': {'port', 'pairing_open'},
                           'network': {'bind'}, 'autostart': set(), 'firewall': set()}
                if not isinstance(action, str) or action not in schemas or set(data) != schemas[action]:
                    raise SetupError('invalid_request')
                return self.reply(202, self.server.backend.submit(action, data))
            return self.reply(404, {'error': 'not_found'})
        except SetupError as error:
            self.reply(400, {'error': str(error)})
            self.close_connection = True
        except (OSError, ValueError):
            self.reply(503, {'error': 'local_service_unavailable'})
            self.close_connection = True

    def do_OPTIONS(self):
        self.reply(403, {'error': 'local_only'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    if sys.platform == 'win32':
        import windows_file_lock as locks
    else:
        import fcntl as locks
    directory = private_dir(STATE / 'console')
    with (directory / 'server.lock').open('a+b') as lock:
        private_file(directory / 'server.lock')
        try:
            locks.flock(lock, locks.LOCK_EX | locks.LOCK_NB)
        except BlockingIOError:
            return
        backend = Backend()
        token = secrets.token_urlsafe(32)
        server = Server(args.port, backend, token)
        record = {'protocol': 1, 'port': server.server_port, 'token': token, 'runtime': str(ROOT)}
        private_write(directory / 'server.json', json.dumps(record).encode())
        stop = threading.Event()
        def maintain():
            while not stop.wait(10):
                try:
                    backend.maintenance()
                except (OSError, ValueError, SetupError):
                    pass
        threading.Thread(target=maintain, daemon=True).start()
        try:
            server.serve_forever(poll_interval=.5)
        except KeyboardInterrupt:
            pass
        finally:
            stop.set()
            server.server_close()


if __name__ == '__main__':
    main()
