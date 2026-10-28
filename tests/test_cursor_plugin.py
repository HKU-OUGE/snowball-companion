import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'snowball-companion'
sys.path.insert(0, str(PLUGIN / 'scripts'))
import cursor_hook as hook
import shared
sys.path.insert(0, str(PLUGIN / 'runtime'))
import status_hub


def event(name, **kwargs):
    return {'hook_event_name': name, 'conversation_id': 'conversation-123',
            'generation_id': 'generation-1', 'workspace_roots': ['/tmp/Project'], **kwargs}


class CursorPluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base / 'config'
        self.root.mkdir(mode=0o700)
        (self.root / 'hub-token').write_text('A' * 44)
        (self.root / 'hub-token').chmod(0o600)
        self.hub = status_hub.StatusHub(self.base / 'hub')

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, name, payload=None):
        return hook.process(name, payload or event(name), root=self.root,
                            sender=lambda record, token: self.hub.accept(record))

    def test_thinking_stop_and_independent_sessions_reach_real_hub(self):
        first = self.send('afterAgentThought')
        self.assertEqual(first['phase'], 'thinking')
        second = self.send('afterAgentThought', event('afterAgentThought', conversation_id='conversation-2'))
        self.assertNotEqual(first['session_id'], second['session_id'])
        done = self.send('stop', event('stop', status='completed'))
        self.assertEqual(done['phase'], 'completed')
        records = self.hub.records
        self.assertEqual(records[self.hub.key(hook.SOURCE, first['session_id'])]['phase'], 'completed')
        self.assertEqual(records[self.hub.key(hook.SOURCE, second['session_id'])]['phase'], 'thinking')

    def test_tool_failure_and_permission_denial_never_imply_waiting_or_terminal_failure(self):
        for kind in ('error', 'timeout', 'permission_denied'):
            result = self.send('postToolUseFailure', event('postToolUseFailure', failure_type=kind))
            self.assertEqual((result['phase'], result['pending_input'], result['questions']), ('thinking', 0, []))
        self.assertEqual(self.send('stop', event('stop', status='error'))['phase'], 'failed')
        self.assertEqual(self.send('afterAgentThought'), None)

    def test_abort_and_session_end_do_not_fake_completion(self):
        self.send('afterAgentThought')
        self.assertEqual(self.send('stop', event('stop', status='aborted'))['phase'], 'idle')
        self.assertEqual(self.send('sessionEnd', event('sessionEnd', reason='window_close'))['phase'], 'idle')

    def test_late_generation_and_late_session_start_do_not_reverse_new_turn(self):
        self.send('afterAgentThought')
        self.send('stop', event('stop', status='completed'))
        self.assertIsNone(self.send('sessionStart'))
        p = event('afterAgentThought', generation_id='generation-2')
        self.assertEqual(self.send('afterAgentThought', p)['phase'], 'thinking')
        self.assertIsNone(self.send('stop', event('stop', status='completed')))
        self.assertIsNone(self.send('postToolUse'))
        self.assertEqual(self.send('stop', event('stop', generation_id='generation-2', status='completed'))['phase'], 'completed')

    def test_no_generation_or_background_never_invent_state(self):
        self.assertIsNone(self.send('afterAgentThought', event('afterAgentThought', generation_id='')))
        self.assertIsNone(self.send('stop', event('stop', status='completed', is_background_agent=True)))
        self.assertIsNone(self.send('postToolUse', event('postToolUse', subagent_id='sub')))
        self.assertEqual(self.hub.records, {})

    def test_private_input_and_tool_output_are_not_transmitted(self):
        payload = event('postToolUse', tool_input={'command': 'SECRET_COMMAND'},
                        tool_output='SECRET_OUTPUT', prompt='SECRET_PROMPT',
                        user_email='SECRET_EMAIL', transcript_path='SECRET_PATH')
        result = self.send('postToolUse', payload)
        raw = json.dumps(result)
        for secret in ('SECRET_COMMAND', 'SECRET_OUTPUT', 'SECRET_PROMPT', 'SECRET_EMAIL', 'SECRET_PATH', 'conversation-123', 'generation-1'):
            self.assertNotIn(secret, raw)
        self.assertEqual(result['display_name'].split(' · ')[1], 'Project')
        self.assertEqual(result['ttl_seconds'], 300)

    def test_delivery_failure_is_not_replayed_and_seq_advances(self):
        def fail(*args):
            raise OSError('offline')
        with self.assertRaises(OSError):
            hook.process('afterAgentThought', event('afterAgentThought'), root=self.root, sender=fail)
        sent = self.send('postToolUse')
        self.assertEqual(sent['seq'], 2)
        self.assertEqual(len(self.hub.records), 1)

    def test_authenticated_loopback_http_to_isolated_real_hub(self):
        server = status_hub.HubServer(('127.0.0.1', 0), self.hub, 'A' * 44)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        old_url = shared.HUB
        shared.HUB = 'http://127.0.0.1:' + str(server.server_address[1])
        thread.start()
        try:
            sent = hook.process('afterAgentThought', event('afterAgentThought'), root=self.root)
            self.assertEqual(sent['phase'], 'thinking')
            stored = self.hub.records[self.hub.key(hook.SOURCE, sent['session_id'])]
            self.assertEqual((stored['provider'], stored['phase']), ('Cursor', 'thinking'))
        finally:
            shared.HUB = old_url
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_manifest_only_observational_hooks_and_empty_output_fail_open(self):
        manifest = json.loads((PLUGIN / '.cursor-plugin/plugin.json').read_text())
        config = json.loads((PLUGIN / 'hooks/hooks.json').read_text())
        self.assertEqual(manifest['name'], 'snowball-companion')
        self.assertEqual(set(config['hooks']), hook.EVENTS)
        self.assertFalse({'preToolUse', 'beforeShellExecution', 'beforeMCPExecution', 'beforeSubmitPrompt'} & set(config['hooks']))
        for name, rows in config['hooks'].items():
            self.assertIn('${CURSOR_PLUGIN_ROOT}/scripts/cursor_hook.py', rows[0]['command'])
            proc = subprocess.run([sys.executable, str(PLUGIN/'scripts/cursor_hook.py'), name], input=b'{bad', capture_output=True, env={**os.environ, 'HOME': str(self.base)})
            self.assertEqual((proc.returncode, proc.stdout.strip(), proc.stderr), (0, b'{}', b''))


if __name__ == '__main__':
    unittest.main()
