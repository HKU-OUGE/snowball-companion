"""Offline source freshness tests; no Bluetooth access or process management."""
import importlib.util
import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('source_bridge', Path(__file__).with_name('bridge.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def snapshot(**changes):
    result = dict(version=1, provider='Codex', state='running', status='known',
                  source_fresh=True, hub_alive=True, generated_at=1000,
                  updated_at=1000, task_updated_at=1000, expires_at=1030,
                  task_fresh=True, task_expires_at=1030,
                  ttl_seconds=30, last_state='running')
    result.update(changes)
    return result


class SourceTests(unittest.TestCase):
    def test_exact_manual_two_keys_are_explicitly_indefinite(self):
        parsed = bridge.parse_source({'provider': 'Claude', 'state': 'waiting'}, now=99999)
        self.assertEqual(parsed, ('Claude', 'waiting', 'manual', None, None, None, ''))

    def test_fresh_hub_preserves_task_timestamp(self):
        parsed = bridge.parse_source(snapshot(), now=1005)
        self.assertEqual(parsed.task_updated_at, 1000)
        self.assertEqual(parsed.mode, 'hub')
        newer_heartbeat = snapshot(updated_at=1004, generated_at=1004, expires_at=1034)
        self.assertEqual(bridge.parse_source(newer_heartbeat, 1005).task_updated_at, 1000)

    def test_expired_snapshot_cannot_be_revived_by_generated_at(self):
        with self.assertRaises(bridge.SourceUnavailable):
            bridge.parse_source(snapshot(generated_at=1100), now=1100)

    def test_stale_with_old_state_never_falls_back_to_manual(self):
        with self.assertRaises(bridge.SourceUnavailable):
            bridge.parse_source(snapshot(source_fresh=False, status='unknown'), now=1005)
        with self.assertRaises(bridge.SourceUnavailable):
            bridge.parse_source({'provider': 'Codex', 'state': 'running', 'source_fresh': False}, now=1005)

    def test_last_state_is_not_an_active_state(self):
        data = snapshot()
        del data['state']
        with self.assertRaises(bridge.SourceUnavailable):
            bridge.parse_source(data, now=1005)

    def test_missing_forged_or_invalid_timestamps_rejected(self):
        for field, value in [('expires_at', 1005), ('expires_at', 9999),
                             ('updated_at', 1011), ('generated_at', 1011),
                             ('task_updated_at', 1011), ('expires_at', float('nan')),
                             ('updated_at', float('inf')), ('task_updated_at', True),
                             ('source_fresh', 1), ('task_fresh', False), ('hub_alive', False),
                             ('task_expires_at', 1005), ('task_expires_at', 9999),
                             ('ttl_seconds', 301), ('version', True),
                             ('state', 'unknown'), ('task_updated_at', None)]:
            with self.subTest(field=field, value=value), self.assertRaises(bridge.SourceUnavailable):
                bridge.parse_source(snapshot(**{field: value}), now=1005)

    def test_source_heartbeat_cannot_revive_expired_task(self):
        renewed_source = snapshot(updated_at=1040, generated_at=1040, expires_at=1070,
                                  source_fresh=True, task_fresh=True)
        with self.assertRaises(bridge.SourceUnavailable):
            bridge.parse_source(renewed_source, now=1041)

    def test_small_future_clock_skew_tolerated(self):
        bridge.parse_source(snapshot(generated_at=1008, updated_at=1008, expires_at=1038), now=1005)


class HubContractTests(unittest.TestCase):
    def test_real_hub_heartbeat_leaves_task_expiry_intact(self):
        hub_path = Path(__file__).with_name('status_hub.py')
        self.assertTrue(hub_path.exists(), 'Status hub is part of this integration')
        hub_spec = importlib.util.spec_from_file_location('contract_hub', hub_path)
        hub_module = importlib.util.module_from_spec(hub_spec)
        hub_spec.loader.exec_module(hub_module)
        clock = [1000.0]
        with tempfile.TemporaryDirectory() as folder:
            hub = hub_module.StatusHub(Path(folder), clock=lambda: clock[0])
            event = dict(version=1, provider='Codex', source='unit-test',
                         session_id='test', event_id='task-1', seq=1, kind='task',
                         timestamp=1000, phase='running', ttl_seconds=30)
            hub.accept(event)
            self.assertEqual(bridge.parse_source(hub.snapshot(), 1000).state, 'running')
            clock[0] = 1025
            heartbeat = {k: v for k, v in event.items() if k != 'phase'}
            heartbeat.update(event_id='heartbeat-2', seq=2, kind='heartbeat', timestamp=1025)
            hub.accept(heartbeat)
            self.assertEqual(bridge.parse_source(hub.snapshot(), 1025).task_updated_at, 1000)
            clock[0] = 1031
            self.assertTrue(hub.snapshot()['source_fresh'])
            with self.assertRaises(bridge.SourceUnavailable):
                bridge.parse_source(hub.snapshot(), 1031)


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_expiry_stops_writes_but_keeps_authenticated_reads(self):
        relay = bridge.StatusRelay()
        target = bridge.parse_source(snapshot(), 1005)
        with patch.object(bridge, 'send', new=AsyncMock(return_value={'applied': 1})) as send, \
             patch.object(bridge, 'read_info', new=AsyncMock(return_value={})) as probe:
            await relay.step(None, {}, target, now=0)
            await relay.step(None, {}, target, now=4.9)
            await relay.step(None, {}, target, now=5)
            self.assertEqual(send.await_count, 2)
            for now in (6, 10, 15, 20, 25):
                await relay.step(None, {}, None, now=now)
            self.assertEqual(send.await_count, 2)
            self.assertEqual(probe.await_count, 4)
            # A recovered source is forwarded immediately, even with the same phase.
            await relay.step(None, {}, target, now=25.1)
            self.assertEqual(send.await_count, 3)

    async def test_metadata_change_updates_same_state_without_new_task_time(self):
        relay = bridge.StatusRelay()
        first = bridge.parse_source(snapshot(session_id='a' * 32, display_name='会话一'), 1005)
        renamed = first._replace(display_name='会话二')
        changed = renamed._replace(session_id='b' * 32)
        with patch.object(bridge, 'send', new=AsyncMock(return_value={'applied': 1})) as send:
            for moment, target in enumerate([first, renamed, changed, changed]):
                await relay.step(None, {}, target, now=moment)
            self.assertEqual(send.await_count, 3)
            self.assertEqual(send.await_args.kwargs['display_name'], '会话二')
            self.assertEqual(send.await_args.kwargs['session_id'], 'b' * 32)
            self.assertEqual(first.task_updated_at, renamed.task_updated_at)
            self.assertEqual(first.valid_until, renamed.valid_until)

    async def test_source_expiring_during_authentication_read_never_writes(self):
        client = AsyncMock()
        with patch.object(bridge, 'read_info', new=AsyncMock(return_value={})), \
             patch.object(bridge.time, 'time', return_value=1031):
            result = await bridge.send(client, {}, 'Codex', 'running', valid_until=1030)
            self.assertIsNone(result)
            client.write_gatt_char.assert_not_awaited()

    async def test_stale_first_source_never_writes_and_disconnect_propagates(self):
        relay = bridge.StatusRelay()
        with patch.object(bridge, 'send', new=AsyncMock()) as send, \
             patch.object(bridge, 'read_info', new=AsyncMock(side_effect=ConnectionError('lost'))):
            with self.assertRaises(ConnectionError):
                await relay.step(None, {}, None, now=0)
            send.assert_not_awaited()

    async def test_heartbeat_does_not_reset_task_timestamp_or_trigger_early_write(self):
        relay = bridge.StatusRelay()
        first = bridge.parse_source(snapshot(), 1005)
        next_heartbeat = bridge.parse_source(snapshot(updated_at=1006, generated_at=1006, expires_at=1036), 1007)
        with patch.object(bridge, 'send', new=AsyncMock(return_value={'applied': 1})) as send:
            await relay.step(None, {}, first, now=0)
            await relay.step(None, {}, next_heartbeat, now=1)
            self.assertEqual(send.await_count, 1)
            self.assertEqual(first.task_updated_at, next_heartbeat.task_updated_at)
            changed = next_heartbeat._replace(state='waiting')
            await relay.step(None, {}, changed, now=1.1)
            self.assertEqual(send.await_count, 2)


if __name__ == '__main__':
    unittest.main()
