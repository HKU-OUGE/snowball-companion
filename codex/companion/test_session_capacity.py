"""Retirement uses isolated private state, never live chats or hardware."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import status_hub
from status_hub import StatusHub, Invalid


class SessionCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.limit = patch.object(status_hub, 'MAX_SESSIONS', 3)
        self.limit.start()
        self.addCleanup(self.limit.stop)
        self.now = 1700000000.0
        self.hub = StatusHub(Path(self.temp.name), lambda: self.now)

    def event(self, name, phase='idle', seq=1, **extra):
        value = dict(version=1, provider='Codex', source='test', session_id=name,
            event_id=f'{name}-{seq}', seq=seq, timestamp=self.now, kind='task',
            phase=phase, ttl_seconds=30)
        value.update(extra)
        self.hub.accept(value)
        return value

    def fill(self):
        self.event('a', display_name='First task')
        self.event('b', 'completed')
        self.event('c')

    def test_expired_history_cannot_block_new_running_task(self):
        self.fill()
        self.now += 31
        self.event('new', 'running')
        value = self.hub.snapshot()
        self.assertEqual(value['state'], 'running')
        self.assertEqual(value['session_id'], 'new')
        self.assertEqual(len(self.hub.records), 3)
        self.assertNotIn('test/a', self.hub.records)
        path = self.hub.history_path('test/a')
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        history = json.loads(path.read_text())
        self.assertEqual(history['record']['display_name'], 'First task')
        self.assertNotIn('test/a', value['priority_order'])

    def test_resumed_history_restores_priority_title_and_replay_guard(self):
        original = self.event('a', display_name='First task')
        self.event('b')
        self.event('c')
        self.now += 31
        self.event('new', 'running')
        before = self.hub.store_path.read_bytes()
        self.assertTrue(self.hub.accept(original)['duplicate'])
        self.assertEqual(self.hub.store_path.read_bytes(), before)
        with self.assertRaises(Invalid):
            self.event('a', 'running', seq=1, event_id='different-1')
        self.hub = StatusHub(Path(self.temp.name), lambda: self.now)
        self.assertEqual(self.hub.priority_order, ['test/a', 'test/b', 'test/c', 'test/new'])
        self.event('a', 'running', seq=2)
        value = self.hub.snapshot()
        self.assertEqual(value['session_id'], 'a')
        self.assertEqual(value['display_name'], 'First task')
        self.assertEqual(len(value['sessions']), 3)
        self.assertEqual(self.hub.priority_order[0], 'test/a')

    def test_no_live_pending_or_unfinished_record_is_retired(self):
        for phase in ('running', 'thinking', 'waiting', 'failed'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                self.hub = StatusHub(Path(tmp), lambda: self.now)
                for name in ('a', 'b', 'c'):
                    self.event(name, phase, pending_input=1 if phase == 'waiting' else 0)
                self.now += 31
                before = self.hub.store_path.read_bytes()
                with self.assertRaisesRegex(Invalid, 'session_capacity_reached'):
                    self.event('new', 'running')
                self.assertEqual(self.hub.store_path.read_bytes(), before)
                self.assertEqual(len(self.hub.records), 3)

    def test_fresh_terminal_history_is_not_evicted(self):
        self.fill()
        before = self.hub.store_path.read_bytes()
        with self.assertRaisesRegex(Invalid, 'session_capacity_reached'):
            self.event('new', 'running')
        self.assertEqual(self.hub.store_path.read_bytes(), before)

    def test_muted_pending_question_survives_capacity_pressure(self):
        self.event('a', 'waiting', pending_input=1,
            questions=[{'id': '1'*32, 'text': 'Question'}])
        self.hub.set_attention({'session': 'test/a', 'question': '1'*32, 'action': 'ignore'})
        self.event('b')
        self.event('c')
        self.now += 31
        self.event('new', 'running')
        self.assertIn('test/a', self.hub.records)
        self.assertEqual(self.hub.records['test/a']['reminders']['1'*32]['mode'], 'ignore')

    def test_archive_failure_preserves_original_records_and_priority(self):
        self.fill()
        self.now += 31
        before = self.hub.store_path.read_bytes()
        order = list(self.hub.priority_order)
        with patch.object(status_hub, 'atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.event('new', 'running')
        self.assertEqual(set(self.hub.records), {'test/a', 'test/b', 'test/c'})
        self.assertEqual(self.hub.priority_order, order)
        self.assertEqual(self.hub.store_path.read_bytes(), before)

    def test_bulk_reorder_preserves_dormant_priority_slot(self):
        self.fill()
        self.now += 31
        self.event('new', 'running')
        self.hub.set_focus({'provider': 'Codex', 'order': ['test/new', 'test/c', 'test/b']})
        self.assertEqual(self.hub.priority_order, ['test/a', 'test/new', 'test/c', 'test/b'])
        self.event('a', 'running', seq=2)
        self.assertEqual(self.hub.snapshot()['session_id'], 'a')

    def test_retired_provider_and_event_identity_remain_bound(self):
        self.fill()
        self.now += 31
        self.event('new', 'running')
        for override in ({'provider': 'Claude', 'seq': 2}, {'seq': 1, 'event_id': 'conflict'}):
            with self.assertRaises(Invalid):
                self.event('a', 'running', **override)
        self.assertNotIn('test/a', self.hub.records)


if __name__ == '__main__':
    unittest.main()
