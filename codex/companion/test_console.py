"""Real loopback HTTP and native filesystem checks; no live services or devices."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import console_backend as backend
import console_launcher as launcher
from console_server import Server
import runtime_lock


class FakeBackend:
    def __init__(self):
        self.calls = []

    def snapshot(self):
        return {'paired': False, 'hub': False, 'dependencies': {}}

    def ports(self):
        return [{'port': 'COM-FIXTURE', 'name': 'Snowball fixture'}]

    def submit(self, action, data):
        self.calls.append((action, data))
        return {'queued': True}

    def provision(self, data):
        self.calls.append(('provision', data))
        return {'ok': True}


class ConsoleHTTP(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.server = Server(0, self.backend, 'test-token-not-a-secret')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        code, headers, body = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertNotIn('Set-Cookie', dict(headers))
        self.assertIn(b'Wi-Fi', body)
        code, _, body = self.request('POST', '/launch', b'{}',
                                    {'Authorization': 'Bearer test-token-not-a-secret', 'Content-Type': 'application/json'})
        self.assertEqual(code, 200)
        self.ticket = json.loads(body)['ticket']
        code, headers, body = self.request('POST', '/api/bootstrap', json.dumps({'ticket': self.ticket}),
                                       {'X-Snowball-UI': '1', 'Content-Type': 'application/json'})
        self.assertEqual(code, 200)
        self.assertNotIn('Set-Cookie', dict(headers))
        self.session = json.loads(body)['session']

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.getheaders(), response.read()
        finally:
            connection.close()

    def auth(self, **extra):
        return {'X-Snowball-Session': self.session, 'X-Snowball-UI': '1', 'Content-Type': 'application/json', **extra}

    def test_browser_session_is_required_for_every_api(self):
        for path in ('/api/status', '/api/ports'):
            self.assertEqual(self.request('GET', path)[0], 403)
            self.assertEqual(self.request('GET', path, headers=self.auth())[0], 200)
        self.assertEqual(self.request('POST', '/api/action', b'{"action":"services"}')[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_cross_origin_and_rebinding_hosts_cannot_read_or_act(self):
        for extra in ({'Origin': 'https://untrusted.invalid'}, {'Host': 'untrusted.invalid'},
                      {'Sec-Fetch-Site': 'cross-site'}, {'Host': 'localhost:' + str(self.server.server_port)}):
            self.assertEqual(self.request('GET', '/api/status', headers=self.auth(**extra))[0], 403)
            self.assertEqual(self.request('POST', '/api/action', b'{"action":"services"}', self.auth(**extra))[0], 400)
        self.assertEqual(self.request('OPTIONS', '/api/action')[0], 403)
        self.assertEqual(self.backend.calls, [])

    def test_no_filesystem_routing_or_credential_endpoint(self):
        for path in ('/../console_backend.py', '/%2e%2e/private', '/api/token', '/server.json'):
            self.assertEqual(self.request('GET', path, headers=self.auth())[0], 404)
        code, headers, body = self.request('GET', '/')
        self.assertNotIn(b'test-token-not-a-secret', body)
        self.assertIn("frame-ancestors 'none'", dict(headers)['Content-Security-Policy'])
        self.assertEqual(dict(headers)['Cache-Control'], 'no-store')

    def test_invalid_or_duplicate_jobs_never_execute(self):
        for body in (b'{"action":"prepare","action":"services"}', b'{"action":[]}',
                     b'{"action":"services","command":"anything"}', b'{"action":"unknown"}'):
            self.assertEqual(self.request('POST', '/api/action', body, self.auth())[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_no_password_response_cookie_log_or_discovery_leak(self):
        secret = 'fixture-password-for-test'
        data = {'ssid': 'fixture network', 'password': secret, 'mode': 1, 'remote': ''}
        log = io.StringIO()
        with contextlib.redirect_stderr(log):
            code, headers, body = self.request('POST', '/api/provision', json.dumps(data), self.auth(Origin=self.server.origin))
        self.assertEqual(code, 200)
        self.assertEqual(self.backend.calls, [('provision', data)])
        self.assertNotIn(secret, str(headers) + body.decode() + log.getvalue())
        self.assertEqual(self.request('GET', '/health')[0], 403)
        self.assertEqual(self.request('GET', '/health', headers={'Authorization': 'Bearer test-token-not-a-secret'})[0], 200)

    def test_expired_session_rejects_mutations(self):
        with self.server.sessions_lock:
            self.server.sessions.clear()
        self.assertEqual(self.request('POST', '/api/action', b'{"action":"services"}', self.auth())[0], 400)
        self.assertEqual(self.backend.calls, [])

    def test_launch_ticket_requires_private_identity_and_cannot_be_replayed(self):
        self.assertEqual(self.request('POST', '/launch', b'{}', {'Content-Type': 'application/json'})[0], 400)
        self.assertEqual(self.request('POST', '/api/bootstrap', json.dumps({'ticket': self.ticket}), self.auth())[0], 400)
        old = self.server.issue_ticket()
        self.server.tickets[old] = time.monotonic() - 61
        self.assertEqual(self.request('POST', '/api/bootstrap', json.dumps({'ticket': old}), self.auth())[0], 400)
        self.assertEqual(self.request('POST', '/api/bootstrap', b'{"ticket":"made-up-token-not-issued-by-launcher"}', self.auth())[0], 400)


class ConsoleBackend(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Snowball O'Brien 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config'
        self.config.mkdir()
        self.state = self.root / 'state'
        self.backend = backend.Backend(self.root / 'companion', self.config, self.state)

    def pair(self):
        data = {'v': 1, 'device': 'Snowball fixture', 'key': '00' * 32}
        (self.config / 'ble-pairing.json').write_text(json.dumps(data), encoding='utf-8')
        return data

    def test_existing_binding_does_not_reopen_usb_or_replace_key(self):
        data = self.pair()
        with patch.object(self.backend, 'ports') as ports, patch('subprocess.run') as run:
            self.assertEqual(self.backend.execute('bind', {'pairing_open': True, 'port': 'COM1'}), 'pairing_reused')
        ports.assert_not_called()
        run.assert_not_called()
        self.assertEqual(backend.pairing(self.config), data)

    def test_usb_automatic_binding_still_requires_an_enumerated_device(self):
        with patch.object(self.backend, 'ports', return_value=[{'port': 'COM9'}]), patch('subprocess.run') as run:
            with self.assertRaisesRegex(backend.SetupError, 'usb_port_missing'):
                self.backend.execute('bind', {'pairing_open': True, 'port': 'COM1'})
            run.assert_not_called()

    def test_credentials_are_not_in_diagnostics(self):
        data = self.pair()
        gateway = {'gateway': True, 'ble': True, 'access': 'must-not-display',
                   'network': {'configured': True, 'sealed': True, 'password': 'must-not-display'}}
        with patch('console_backend.network_status', return_value=gateway), patch('console_backend.hub_health', return_value=True), patch('console_backend.lan_addresses', return_value=[]), patch.object(self.backend, 'dependencies', return_value={}):
            value = self.backend.snapshot()
        self.assertNotIn(data['key'], json.dumps(value))
        self.assertNotIn('must-not-display', json.dumps(value))
        self.assertTrue(value['network']['network']['sealed'])

    def test_usb_failure_preserves_only_fixed_actionable_error_codes(self):
        cases = [(b'XQUSB_ERROR {"code":"usb_pairing_confirmation_required"}', 'usb_pairing_confirmation_required'),
                 (b'XQUSB_ERROR {"code":"usb_device_not_ready"}', 'usb_device_not_ready'),
                 (b'XQUSB_ERROR {"code":"usb_no_response"}', 'usb_no_response'),
                 (b'PermissionError: private-device-path', 'usb_binding_failed'),
                 (b'XQUSB_ERROR {"code":"private-key-must-not-leak"}', 'usb_binding_failed')]
        for stderr, expected in cases:
            with self.subTest(expected=expected), patch.object(self.backend, 'ports', return_value=[{'port': 'COM9'}]), patch('subprocess.run', return_value=subprocess.CompletedProcess([], 2, stdout=b'', stderr=stderr)):
                with self.assertRaisesRegex(backend.SetupError, '^' + expected + '$'):
                    self.backend.execute('bind', {'port': 'COM9'})
                self.assertFalse((self.config / 'ble-pairing.json').exists())

    def test_stale_or_other_device_telemetry_cannot_claim_connected(self):
        self.pair()
        directory = self.state / 'device'
        directory.mkdir(parents=True)
        value = {'device': 'Snowball fixture', 'source': 'device', 'received_at': time.time(), 'connected': True, 'transport': 'ble', 'status_current': True}
        for changes, expected in (({}, True), ({'received_at': time.time() - 60}, False), ({'device': 'different'}, False), ({'source': 'demo'}, False)):
            (directory / 'latest.json').write_text(json.dumps(dict(value, **changes)), encoding='utf-8')
            self.assertEqual(self.backend.telemetry()['connected'], expected)

    def test_capabilities_report_running_hub_without_private_question_content(self):
        directory = self.state / 'hub'
        directory.mkdir(parents=True)
        value = {'generated_at': time.time(), 'hub_alive': True, 'can_create': True,
                 'sessions': [{'session_id': 'private-session', 'title': 'private-title',
                               'instruction_available': True,
                               'questions': [{'text': 'private-question', 'answerable': True},
                                             {'status': 'ignored', 'answerable': True},
                                             {'answerable': False}]}]}
        backend.private_write(directory / 'snapshot.json', json.dumps(value).encode())
        self.assertEqual(self.backend.capabilities(), {'fresh': True, 'can_create': True,
                         'can_instruct': True, 'pending': 2, 'answerable': 1})
        self.assertNotIn('private-', json.dumps(self.backend.capabilities()))
        for change in ({'generated_at': time.time() - 60}, {'generated_at': time.time() + 60},
                       {'hub_alive': False}, {'sessions': None}, {'sessions': ['invalid']}):
            backend.private_write(directory / 'snapshot.json', json.dumps(dict(value, **change)).encode())
            self.assertFalse(self.backend.capabilities()['fresh'])

    def test_healthy_hub_without_answer_channel_is_not_reported_as_answerable(self):
        directory = self.state / 'hub'
        directory.mkdir(parents=True)
        value = {'generated_at': time.time(), 'hub_alive': True, 'can_create': False,
                 'sessions': [{'instruction_available': False,
                               'questions': [{'answerable': False}]}]}
        backend.private_write(directory / 'snapshot.json', json.dumps(value).encode())
        self.assertEqual(self.backend.capabilities(), {'fresh': True, 'can_create': False,
                         'can_instruct': False, 'pending': 1, 'answerable': 0})

    def test_unknown_job_and_overlapping_jobs_are_rejected(self):
        with self.assertRaisesRegex(backend.SetupError, 'unknown_action'):
            self.backend.submit('shell', {})
        entered, release = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            release.wait(3)
            return 'prepared'
        with patch.object(self.backend, 'execute', side_effect=slow):
            self.backend.submit('prepare', {})
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(backend.SetupError, 'job_busy'):
                self.backend.submit('prepare', {})
            release.set()
            until = time.monotonic() + 2
            while self.backend.job['state'] == 'running' and time.monotonic() < until:
                time.sleep(.01)
        self.assertEqual(self.backend.job['state'], 'done')

    def test_invalid_remote_and_wifi_fields_do_not_reach_gateway(self):
        good = {'ssid': 'fixture', 'password': '12345678', 'mode': 1, 'remote': ''}
        with patch('console_backend.local_api') as api:
            for value in (dict(good, mode=True), dict(good, ssid='中' * 20),
                          dict(good, remote='http://example.invalid:443'), dict(good, password='short')):
                with self.assertRaises(backend.SetupError):
                    self.backend.provision(value)
            api.assert_not_called()

    def test_shared_bridge_lock_prevents_another_process(self):
        directory = self.root / 'locks'
        handle = runtime_lock.acquire('bridge', directory)
        self.assertIsNotNone(handle)
        code = 'import sys;from pathlib import Path;import runtime_lock;h=runtime_lock.acquire("bridge",Path(sys.argv[1]));sys.exit(7 if h is None else 0)'
        try:
            result = subprocess.run([sys.executable, '-c', code, str(directory)], cwd=Path(runtime_lock.__file__).parent)
            self.assertEqual(result.returncode, 7)
        finally:
            handle.close()
        result = subprocess.run([sys.executable, '-c', code, str(directory)], cwd=Path(runtime_lock.__file__).parent)
        self.assertEqual(result.returncode, 0)


class Launcher(unittest.TestCase):
    def test_real_server_launcher_and_duplicate_instance_in_isolated_home(self):
        with tempfile.TemporaryDirectory(prefix="Snowball console 中文 ") as folder:
            home = Path(folder)
            env = dict(os.environ, HOME=folder, USERPROFILE=folder, CODEX_HOME=str(home / '.codex'), PYTHONUTF8='1')
            env.pop('CODEX_APP_TOOLS_PIPE_PATH', None)
            root = Path(launcher.__file__).parent
            process = subprocess.Popen([sys.executable, '-X', 'utf8', str(root / 'console_server.py')],
                                       env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                record_path = home / '.local/state/xueqiu/console/server.json'
                until = time.monotonic() + 10
                while not record_path.exists() and time.monotonic() < until and process.poll() is None:
                    time.sleep(.05)
                self.assertTrue(record_path.exists(), 'Actual console server did not create discovery state')
                record = json.loads(record_path.read_text(encoding='utf-8'))
                duplicate = subprocess.run([sys.executable, '-X', 'utf8', str(root / 'console_server.py')],
                                           env=env, capture_output=True, timeout=5)
                self.assertEqual(duplicate.returncode, 0, duplicate.stderr.decode('utf-8', 'replace'))
                opened = subprocess.run([sys.executable, '-X', 'utf8', str(root / 'console_launcher.py'), 'open', '--no-browser'],
                                        env=env, capture_output=True, timeout=10)
                self.assertEqual(opened.returncode, 0, opened.stderr.decode('utf-8', 'replace'))
                self.assertTrue(opened.stdout.decode().strip().startswith(f'http://127.0.0.1:{record["port"]}/#setup='))
                self.assertNotIn(record['token'], opened.stdout.decode())
                self.assertEqual(json.loads(record_path.read_text(encoding='utf-8')), record)
                self.assertTrue((home / '.config/xueqiu/console-installation.json').is_file())
                self.assertFalse((home / '.config/xueqiu/ble-pairing.json').exists())
                self.assertFalse((home / '.config/xueqiu/network/gateway.json').exists())
            finally:
                process.terminate()
                process.communicate(timeout=5)

    def test_discovery_requires_authenticated_application_identity(self):
        fake = FakeBackend()
        server = Server(0, fake, 'a' * 43)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                record = {'protocol': 1, 'port': server.server_port, 'token': 'b' * 43}
                (root / 'server.json').write_text(json.dumps(record), encoding='utf-8')
                self.assertIsNone(launcher.active_url(root))
                record['token'] = 'a' * 43
                (root / 'server.json').write_text(json.dumps(record), encoding='utf-8')
                self.assertEqual(launcher.active_url(root), server.origin + '/')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    unittest.main()
