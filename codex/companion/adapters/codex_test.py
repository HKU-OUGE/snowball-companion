#!/usr/bin/env python3
"""Synthetic contract tests. These never launch Codex or an AI task."""
import json
import os
import sqlite3
from unittest.mock import patch
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import codex_hook as adapter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from status_hub import StatusHub


class CodexAdapterTests(unittest.TestCase):
    def test_exact_readonly_metadata_lookup_and_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state_5.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, title TEXT, first_user_message TEXT)')
                db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('exact', '雪球界面同步 🐶', 'SECRET_BODY'))
                db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('other', '不要显示', 'SECRET_OTHER'))
            before = path.read_bytes()
            self.assertEqual(adapter.session_title('exact', directory), '雪球界面同步 🐶')
            self.assertIsNone(adapter.session_title("exact' OR 1=1 --", directory))
            self.assertIsNone(adapter.session_title('missing', directory))
            self.assertEqual(path.read_bytes(), before)

    def test_desktop_name_preferred_and_no_prompt_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state_5.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT, title TEXT)')
                db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('named', '创建像素卡通小宠物', 'SECRET_FIRST_PROMPT'))
                db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('unnamed', None, 'SECRET_FIRST_PROMPT'))
                db.execute('INSERT INTO threads VALUES (?, ?, ?)', ('cleared', '', 'SECRET_FIRST_PROMPT'))
            self.assertEqual(adapter.session_title('named', directory), '创建像素卡通小宠物')
            self.assertIsNone(adapter.session_title('unnamed', directory))
            self.assertEqual(adapter.session_title('cleared', directory), '')

    def test_unavailable_name_preserves_hub_title_and_explicit_empty_clears(self):
        for failure in ('timeout', 'missing_row', 'null_name', 'empty_name'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                codex_home = root / 'codex'
                codex_home.mkdir()
                database = codex_home / 'state_5.sqlite'
                with sqlite3.connect(database) as db:
                    db.execute('CREATE TABLE threads (id TEXT PRIMARY KEY, name TEXT)')
                    db.execute('INSERT INTO threads VALUES (?, ?)', ('exact', '已有真实会话名'))
                hub = StatusHub(root / 'hub')
                sent = []
                def emit(event):
                    sent.append(event)
                    hub.accept(event)
                payload = {'hook_event_name': 'UserPromptSubmit', 'session_id': 'exact'}
                with patch.dict(os.environ, {'CODEX_HOME': str(codex_home)}):
                    adapter.process_hook(payload, root / 'adapter', emit)
                    self.assertEqual(hub.snapshot()['display_name'], '已有真实会话名')
                    payload['hook_event_name'] = 'PreToolUse'
                    if failure == 'timeout':
                        with patch.object(adapter.sqlite3, 'connect', side_effect=sqlite3.OperationalError('database is locked')):
                            adapter.process_hook(payload, root / 'adapter', emit)
                    else:
                        with sqlite3.connect(database) as db:
                            if failure == 'missing_row':
                                db.execute('DELETE FROM threads WHERE id = ?', ('exact',))
                            else:
                                db.execute('UPDATE threads SET name = ? WHERE id = ?',
                                           (None if failure == 'null_name' else '', 'exact'))
                        adapter.process_hook(payload, root / 'adapter', emit)
                if failure == 'empty_name':
                    self.assertEqual(sent[-1]['display_name'], '')
                    self.assertEqual(hub.snapshot()['display_name'], '')
                else:
                    self.assertNotIn('display_name', sent[-1])
                    self.assertEqual(hub.snapshot()['display_name'], '已有真实会话名')
                self.assertEqual(hub.snapshot()['state'], 'running')

    def test_title_budget_and_controls(self):
        self.assertEqual(adapter.bounded_title('汉' * 100), '汉' * 64)
        self.assertEqual(adapter.bounded_title('🐶' * 100), '🐶' * 48)
        self.assertEqual(adapter.bounded_title('A\x00\u202e B'), 'A B')
        self.assertIsNone(adapter.bounded_title(None))
        self.assertIsNone(adapter.bounded_title('  '))

    def test_authorized_title_is_metadata_not_payload_guess(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(adapter, 'session_title', return_value='真实会话名'):
            sent = []
            adapter.process_hook({'hook_event_name': 'PreToolUse', 'session_id': 'exact',
                                  'title': 'UNTRUSTED_PAYLOAD_TITLE', 'prompt': 'SECRET'}, Path(directory), sent.append)
            self.assertEqual(sent[0]['display_name'], '真实会话名')
            self.assertEqual(sent[0]['session_id'], adapter.opaque('exact'))

    def test_redirect_cannot_forward_local_token(self):
        request = urllib.request.Request('http://127.0.0.1:4190/v1/events',
                                         headers={'Authorization': 'Bearer synthetic'})
        with self.assertRaises(urllib.error.HTTPError):
            adapter.NoRedirect().redirect_request(request, None, 302, 'Found', {}, 'https://example.invalid/collect')

    def test_parallel_tools_and_permission(self):
        state = {}
        def phase(name, tool=None):
            return adapter.hook_phase({'hook_event_name': name, 'tool_use_id': tool}, state)
        self.assertEqual(phase('UserPromptSubmit'), 'thinking')
        self.assertEqual(phase('PreToolUse', 'one'), 'running')
        self.assertEqual(phase('PreToolUse', 'two'), 'running')
        self.assertEqual(phase('PermissionRequest', 'one'), 'running')
        self.assertEqual(phase('PostToolUse', 'two'), 'running')
        self.assertEqual(phase('PostToolUse', 'one'), 'thinking')
        self.assertEqual(phase('Stop'), 'completed')

    def test_parallel_completion_still_running(self):
        state = {}
        for tool in ('one', 'two'):
            adapter.hook_phase({'hook_event_name': 'PreToolUse', 'tool_use_id': tool}, state)
        self.assertEqual(adapter.hook_phase({'hook_event_name': 'PostToolUse', 'tool_use_id': 'one'}, state), 'running')

    def test_private_fields_never_persisted_or_sent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sent = []
            adapter.process_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'private-session',
                'prompt': 'SECRET_PROMPT', 'cwd': '/SECRET_PATH', 'transcript_path': '/SECRET_TRANSCRIPT',
                'tool_input': {'command': 'SECRET_COMMAND'}, 'last_assistant_message': 'SECRET_REPLY'}, root, sent.append)
            data = json.dumps(sent) + ''.join(p.read_text() for p in root.rglob('*.json'))
            self.assertNotIn('SECRET_', data)
            self.assertNotIn('private-session', data)
            self.assertEqual(sent[0]['phase'], 'thinking')
            self.assertEqual(set(sent[0]), {'version', 'provider', 'source', 'session_id', 'event_id', 'seq', 'timestamp', 'kind', 'phase', 'ttl_seconds', 'pending_input'})

    def test_unknown_event_ignored(self):
        self.assertIsNone(adapter.hook_phase({'hook_event_name': 'MadeUpFailure'}, {}))

    def test_concurrent_sequences_unique_and_ordered(self):
        with tempfile.TemporaryDirectory() as directory:
            sent = []
            def invoke(_):
                adapter.process_hook({'hook_event_name': 'PreCompact', 'session_id': 'parallel'}, Path(directory), sent.append)
            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(invoke, range(32)))
            self.assertEqual([event['seq'] for event in sent], list(range(1, 33)))

    def test_offline_delivery_does_not_reuse_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = {'hook_event_name': 'Stop', 'session_id': 'offline'}
            def offline(_):
                raise OSError('connection unavailable')
            adapter.process_hook(payload, Path(directory), offline)
            sent = []
            adapter.process_hook(payload, Path(directory), sent.append)
            self.assertEqual(sent[0]['seq'], 2)

    def test_hook_fails_open_with_valid_empty_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(adapter.__file__)), '--state-dir', directory,
                                     '--token-file', directory + '/missing', 'hook'],
                                    input=json.dumps({'hook_event_name': 'Stop', 'session_id': 'offline'}),
                                    text=True, capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '{}\n')
            self.assertEqual(result.stderr, '')

    def test_malformed_hook_fails_open(self):
        result = subprocess.run([sys.executable, str(Path(adapter.__file__)), 'hook'], input='not json',
                                text=True, capture_output=True, timeout=3)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (0, '{}\n', ''))

    def test_jsonl_only_explicit_terminal_failures(self):
        reducer = adapter.JsonlReducer()
        self.assertIsNone(reducer.reduce({'type': 'turn.completed'}))
        self.assertEqual(reducer.reduce({'type': 'thread.started', 'thread_id': 'test'}), 'idle')
        self.assertEqual(reducer.reduce({'type': 'turn.started'}), 'thinking')
        self.assertEqual(reducer.reduce({'type': 'item.started', 'item': {'id': '1', 'type': 'command_execution', 'command': 'secret'}}), 'running')
        self.assertEqual(reducer.reduce({'type': 'item.completed', 'item': {'id': '1', 'type': 'command_execution', 'exit_code': 1}}), 'thinking')
        self.assertIsNone(reducer.reduce({'type': 'error', 'message': 'transient retry'}))
        self.assertEqual(reducer.reduce({'type': 'turn.failed'}), 'failed')

    def test_user_input_tool_is_waiting(self):
        self.assertEqual(adapter.hook_phase({'hook_event_name': 'PreToolUse', 'tool_use_id': 'a',
                                            'tool_name': 'request_user_input'}, {}), 'waiting')

    def test_export_is_self_contained_and_not_install(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / 'plugin'
            adapter.export_plugin(destination)
            config = json.loads((destination / 'hooks/hooks.json').read_text())
            self.assertEqual(set(config['hooks']), set(adapter.HOOKS))
            self.assertEqual((destination / 'scripts/codex_hook.py').read_bytes(), Path(adapter.__file__).read_bytes())
            self.assertNotIn('dangerously', json.dumps(config))
            with self.assertRaises(FileExistsError):
                adapter.export_plugin(destination)


if __name__ == '__main__':
    unittest.main()
