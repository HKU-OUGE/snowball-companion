import concurrent.futures
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from status_hub import HubServer, Invalid, StatusHub, atomic_json, load_token, strict_json


class HubTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = 1700000000.0
        self.hub = StatusHub(Path(self.tmp.name) / 'state', lambda: self.now)

    def tearDown(self):
        self.tmp.cleanup()

    def event(self, **extra):
        event = dict(version=1, provider='Codex', source='test-source', session_id='opaque-1',
                     event_id='event-1', seq=1, timestamp=self.now, kind='task', phase='running')
        event.update(extra)
        return event

    def test_display_name_preserved_and_metadata_heartbeat_does_not_renew_task(self):
        self.hub.accept(self.event(display_name='雪球任务 🐶'))
        value = self.hub.snapshot()
        self.assertEqual(value['display_name'], '雪球任务 🐶')
        self.assertEqual(value['sessions'][0]['display_name'], '雪球任务 🐶')
        task_expiry = value['task_expires_at']
        self.now += 20
        event = self.event(display_name='重命名', seq=2, event_id='rename', kind='heartbeat')
        del event['phase']
        self.hub.accept(event)
        value = self.hub.snapshot()
        self.assertEqual(value['display_name'], '重命名')
        self.assertEqual(value['task_expires_at'], task_expiry)
        self.now += 11
        self.assertNotIn('state', self.hub.snapshot())
        self.assertEqual(self.hub.snapshot()['display_name'], '重命名')

    def test_display_name_clear_and_invalid_unicode_limits(self):
        for title in ['汉' * 65, '🐶' * 49, 'nul\x00', 'line\n', '\ud800', '\u202ehidden', 42]:
            with self.subTest(title=repr(title)), self.assertRaises(Invalid):
                self.hub.accept(self.event(display_name=title))
        self.hub.accept(self.event(display_name='汉' * 64))
        self.hub.accept(self.event(seq=2, event_id='clear', display_name=''))
        self.assertEqual(self.hub.snapshot()['display_name'], '')

    def test_state_and_private_atomic_snapshot(self):
        self.hub.accept(self.event())
        data = json.loads(self.hub.snapshot_path.read_text())
        self.assertEqual(data['state'], 'running')
        self.assertTrue(data['source_fresh'])
        self.assertEqual(os.stat(self.hub.snapshot_path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.hub.state_dir).st_mode & 0o777, 0o700)

    def test_timeout_is_unknown_not_failure_or_idle(self):
        self.hub.accept(self.event())
        self.now += 31
        value = self.hub.publish()
        self.assertNotIn('state', value)
        self.assertEqual(value['last_state'], 'running')
        self.assertEqual(value['status'], 'unknown')
        self.assertFalse(value['source_fresh'])

    def test_terminal_history_survives_source_timeout(self):
        for phase in ['completed', 'failed']:
            self.hub.accept(self.event(phase=phase, seq=2 if phase == 'failed' else 1,
                                       event_id=phase))
            self.now += 31
            value = self.hub.snapshot()
            self.assertEqual(value['last_state'], phase)
            self.assertTrue(value['terminal'])
            self.assertFalse(value['source_fresh'])
            if phase == 'completed':
                self.assertEqual(value['state'], 'idle')
                self.assertEqual(value['status'], 'ready')
            else:
                self.assertNotIn('state', value)

    def test_heartbeat_preserves_task_timestamp_and_phase(self):
        self.hub.accept(self.event())
        self.now += 20
        event = self.event(seq=2, event_id='heartbeat-2', kind='heartbeat')
        del event['phase']
        self.hub.accept(event)
        value = self.hub.snapshot()
        self.assertEqual(value['state'], 'running')
        self.assertEqual(value['task_updated_at'], self.now - 20)
        self.assertEqual(value['source_last_seen'], self.now)
        self.assertIsNone(value['radio_connected'])
        self.assertIsNone(value['bridge_alive'])
        self.now += 11
        value = self.hub.snapshot()
        self.assertTrue(value['source_fresh'])
        self.assertFalse(value['task_fresh'])
        self.assertNotIn('state', value)
        self.assertEqual(value['last_state'], 'running')

    def test_duplicate_does_not_extend_ttl(self):
        event = self.event()
        self.hub.accept(event)
        self.now += 29
        self.assertTrue(self.hub.accept(event)['duplicate'])
        self.now += 2
        self.assertFalse(self.hub.snapshot()['source_fresh'])

    def test_replays_and_out_of_order_rejected(self):
        self.hub.accept(self.event(seq=5))
        variants = [self.event(seq=4, event_id='event-2'),
                    self.event(seq=6, phase='failed'),
                    self.event(seq=6, event_id='event-2', timestamp=self.now-1)]
        for event in variants:
            with self.assertRaises(Invalid):
                self.hub.accept(event)
        self.assertEqual(self.hub.snapshot()['state'], 'running')

    def test_fields_and_time_are_strict(self):
        invalid = [dict(prompt='private text'), dict(command='rm something'), dict(foo=True),
                   dict(seq=True), dict(seq=0), dict(timestamp=float('nan')),
                   dict(timestamp=self.now-121), dict(timestamp=self.now+6),
                   dict(ttl_seconds=301), dict(session_id='../session'), dict(source=''),
                   dict(provider='Other'), dict(kind='heartbeat'), dict(phase='unknown')]
        for override in invalid:
            with self.subTest(override=list(override)):
                with self.assertRaises(Invalid):
                    self.hub.accept(self.event(**override))
        with self.assertRaises(Invalid):
            strict_json('{"a":1,"a":2}')

    def test_delayed_event_cannot_gain_full_new_ttl(self):
        self.hub.accept(self.event(timestamp=self.now-29))
        self.now += 2
        self.assertFalse(self.hub.snapshot()['source_fresh'])

    def test_focus_pinned_and_auto(self):
        self.hub.accept(self.event())
        self.hub.set_focus({'source':'test-source', 'session_id':'opaque-1'})
        self.now += 1
        self.hub.accept(self.event(session_id='opaque-2', phase='waiting'))
        self.assertEqual(self.hub.snapshot()['state'], 'running')
        self.hub.set_focus({'auto':True})
        self.assertEqual(self.hub.snapshot()['state'], 'waiting')
        self.assertEqual(len(self.hub.snapshot()['sessions']), 2)
        with self.assertRaises(Invalid):
            self.hub.set_focus({'source':'test-source','session_id':'missing'})

    def test_restart_preserves_replay_guard_and_expiry(self):
        event = self.event()
        self.hub.accept(event)
        self.now += 31
        restarted = StatusHub(self.hub.state_dir, lambda: self.now)
        self.assertFalse(restarted.snapshot()['source_fresh'])
        self.assertTrue(restarted.accept(event)['duplicate'])
        self.assertFalse(restarted.snapshot()['source_fresh'])

    def test_heartbeat_without_task_has_no_task_activity(self):
        event = self.event(kind='heartbeat')
        del event['phase']
        self.hub.accept(event)
        value = self.hub.snapshot()
        self.assertEqual(value['state'], 'idle')
        self.assertEqual(value['status'], 'ready')
        self.assertFalse(value['sessions'][0]['task_fresh'])
        self.assertIsNone(value['sessions'][0]['task_updated_at'])

    def test_provider_cannot_change_session_identity(self):
        self.hub.accept(self.event())
        with self.assertRaises(Invalid):
            self.hub.accept(self.event(provider='Claude',seq=2,event_id='event-2'))

    def test_atomic_concurrent_readers_never_observe_partial_json(self):
        path = Path(self.tmp.name) / 'atomic.json'
        atomic_json(path, {'index':0, 'padding':'x'*1000})
        def reader():
            for _ in range(250):
                value = json.loads(path.read_text())
                assert len(value['padding']) == 1000
        with concurrent.futures.ThreadPoolExecutor() as pool:
            readers = [pool.submit(reader) for _ in range(3)]
            for index in range(50):
                atomic_json(path, {'index':index,'padding':'x'*1000})
            for future in readers:
                future.result()

    def test_token_private_and_stable(self):
        path = Path(self.tmp.name) / 'config' / 'token'
        token = load_token(path)
        self.assertEqual(token, load_token(path))
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)


class HttpTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hub = StatusHub(Path(self.tmp.name))
        self.token = 'test-only-token-not-installed-' + 'x'*32
        self.server = HubServer(('127.0.0.1', 0), self.hub, self.token)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, method, path, payload=None, auth=True, extra=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        headers = {'Content-Type':'application/json'}
        if auth:
            headers['Authorization'] = 'Bearer ' + self.token
        headers.update(extra or {})
        data = json.dumps(payload) if payload is not None else None
        connection.request(method, path, data, headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    def test_authentication_required_for_all_routes(self):
        for method, path in [('GET','/v1/health'),('GET','/v1/snapshot'),
                             ('POST','/v1/events'),('POST','/v1/focus')]:
            status, _ = self.request(method,path,auth=False)
            self.assertEqual(status,401)
        self.assertEqual(self.request('GET','/v1/health')[0],200)

    def test_origin_and_host_denied(self):
        for header in [{'Origin':'https://example.com'}, {'Host':'attacker.example'}]:
            self.assertEqual(self.request('GET','/v1/snapshot',extra=header)[0],403)

    def test_body_limit_and_invalid_content(self):
        self.assertEqual(self.request('POST','/v1/events',{'prompt':'x'*65536})[0],413)
        self.assertEqual(self.request('POST','/v1/events',[])[0],400)
        self.assertEqual(self.request('POST','/v1/events',{},extra={'Content-Type':'text/plain'})[0],415)

    def test_live_post_and_snapshot(self):
        import time
        event = dict(version=1,provider='Cursor',source='test',session_id='abc',
                     event_id='event-1',seq=1,timestamp=time.time(),kind='task',phase='thinking')
        self.assertEqual(self.request('POST','/v1/events',event)[0],200)
        self.assertTrue(self.request('POST','/v1/events',event)[1]['duplicate'])
        snapshot = self.request('GET','/v1/snapshot')[1]
        self.assertEqual(snapshot['provider'],'Cursor')
        self.assertEqual(snapshot['state'],'thinking')
        self.assertEqual(snapshot['last_state'],'thinking')
        self.assertEqual(self.request('POST','/v1/focus',{'source':'test','session_id':'abc'})[0],200)


if __name__ == '__main__':
    unittest.main()
