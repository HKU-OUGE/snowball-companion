"""Portable latency/single-writer regressions. No live host, radio or credentials."""
import hmac
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from unittest.mock import AsyncMock

import bridge
from attention_relay import AttentionRelay, SummaryRelay

KEY = bytes(range(32))
CONFIG = {'device': 'Fixture', 'key': KEY.hex()}
NONCE = '0011223344556677'


def snapshot(state='idle', **changes):
    now = time.time()
    value = dict(version=1, provider='Codex', state=state, status='known', source_fresh=True,
                 task_fresh=True, hub_alive=True, generated_at=now, updated_at=now,
                 task_updated_at=now, expires_at=now+30, task_expires_at=now+30,
                 ttl_seconds=30, session_id='fixture', source='codex-hooks', display_name='Fixture')
    value.update(changes)
    return value


class Device:
    def __init__(self):
        self.accepted = self.applied = self.reads = 0
        self.provider = self.state = 0
        self.writes = []
        self.fail_write = False

    async def read_gatt_char(self, _):
        self.reads += 1
        value = dict(v=1, device=CONFIG['device'], nonce=NONCE, accepted=self.accepted,
                     applied=self.applied, provider=self.provider, state=self.state,
                     catalog_v=3, attention_v=2)
        value['proof'] = hmac.digest(KEY, b'XQINFO'+struct.pack('<8sIIBB', bytes.fromhex(NONCE),
            self.accepted, self.applied, self.provider, self.state), 'sha256').hex()
        return json.dumps(value).encode()

    async def write_gatt_char(self, _, packet, response=True):
        if self.fail_write:
            raise OSError('fixture write failure')
        assert hmac.compare_digest(packet[-32:], hmac.digest(KEY, packet[:-32], 'sha256'))
        version, provider, op, seq = struct.unpack('<BBBI', packet[:7])
        assert seq == self.accepted+1, (seq, self.accepted)
        self.accepted = seq
        self.writes.append((version, op, seq))
        if version in (1, 2):
            self.applied, self.provider, self.state = seq, provider, op


class FreshInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_ui_ack_is_polled_fresh_until_applied(self):
        class DelayedDevice(Device):
            pending = None
            async def write_gatt_char(self, *args, **kwargs):
                previous = self.applied
                await super().write_gatt_char(*args, **kwargs)
                self.pending, self.applied = self.applied, previous
            async def read_gatt_char(self, uuid):
                raw = await super().read_gatt_char(uuid)
                if self.pending is not None:
                    self.applied, self.pending = self.pending, None
                return raw
        client = DelayedDevice()
        bridge.observe_client_writes(client)
        with patch.object(bridge.asyncio, 'sleep', AsyncMock()):
            ack = await bridge.send(client, CONFIG, 'Codex', 'thinking')
        self.assertEqual(ack['applied'], 1)
        self.assertEqual(client.reads, 3)

    async def test_bounded_reuse_copy_and_each_write_invalidates(self):
        client = Device()
        bridge.observe_client_writes(client)
        with patch.object(bridge.time, 'monotonic', return_value=10):
            first = await bridge.read_info(client, CONFIG)
            first['request'] = {'pending': 'do not cache this mutation'}
            second = await bridge.read_info(client, CONFIG)
            self.assertNotIn('request', second)
            self.assertEqual(client.reads, 1)
            packet = bridge.make_packet(KEY, second, 'Codex', 'thinking')
            await client.write_gatt_char(bridge.WRITE, packet)
            ack = await bridge.read_info(client, CONFIG)
            self.assertEqual((client.reads, ack['applied'], ack['state']), (2, 1, 1))
        with patch.object(bridge.time, 'monotonic', return_value=10.201):
            await bridge.read_info(client, CONFIG)
        self.assertEqual(client.reads, 3)

    async def test_failed_write_different_key_and_wifi_cannot_reuse(self):
        client = Device()
        bridge.observe_client_writes(client)
        await bridge.read_info(client, CONFIG)
        with self.assertRaises(ValueError):
            await bridge.read_info(client, dict(CONFIG, key='00'*32))
        client.fail_write = True
        with self.assertRaises(OSError):
            await client.write_gatt_char(bridge.WRITE, b'fixture')
        await bridge.read_info(client, CONFIG)
        self.assertEqual(client.reads, 2)
        client.transport = 'wifi'
        await bridge.read_info(client, CONFIG)
        await bridge.read_info(client, CONFIG)
        self.assertEqual(client.reads, 4)


class StatusPriorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_to_work_does_not_wait_for_heartbeat_and_stale_never_writes(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)/'snapshot.json'
            source.write_text(json.dumps(snapshot()), encoding='utf-8')
            client = Device()
            bridge.observe_client_writes(client)
            pump = bridge.LiveStatusPump(source, bridge.StatusRelay(), report=lambda _: None)
            self.assertTrue(await pump.step(client, CONFIG, force=True))
            source.write_text(json.dumps(snapshot('thinking')), encoding='utf-8')
            self.assertTrue(await pump.step(client, CONFIG, force=True))
            self.assertEqual([row[1] for row in client.writes], [0, 1])
            source.write_text(json.dumps(snapshot('running', source_fresh=False)), encoding='utf-8')
            self.assertFalse(await pump.step(client, CONFIG, force=True))
            self.assertIsNone(pump.target)
            self.assertEqual(len(client.writes), 2)

    async def check_preemption(self, kind):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)/'snapshot.json'
            source.write_text(json.dumps(snapshot()), encoding='utf-8')
            client = Device()
            bridge.observe_client_writes(client)
            pump = bridge.LiveStatusPump(source, bridge.StatusRelay(), report=lambda _: None)
            await pump.step(client, CONFIG, force=True)
            client.writes.clear()
            now = time.time()
            row = dict(provider='Codex', source='codex-hooks', session_id='fixture', phase='idle',
                display_name='Fixture', source_fresh=True, task_fresh=True, expires_at=now+30,
                task_expires_at=now+30, sidebar_visible=True, summary={'kind':'progress','text':'摘要'*128},
                questions=[{'id':'a'*32, 'text':'问题'*128, 'status':'active', 'options':['选项一','选项二'], 'answerable':True}])
            payload = dict(version=1, hub_alive=True, generated_at=now, sessions=[row])
            triggered = False
            async def service():
                nonlocal triggered
                if not triggered and client.writes[-1][:2] == (kind, 1):
                    source.write_text(json.dumps(snapshot('thinking')), encoding='utf-8')
                    triggered = True
                return await pump.step(client, CONFIG, force=True)
            with patch.object(bridge, 'verify_request', return_value=None):
                catalog = bridge.CatalogRelay()
                if kind == 3:
                    await catalog.step(client, CONFIG, payload, service_state=service)
                elif kind == 6:
                    relay = AttentionRelay(bridge.read_info, lambda *_: None)
                    await relay.step(client, CONFIG, payload, service_state=service)
                else:
                    relay = SummaryRelay(bridge.read_info, catalog.catalogue)
                    await relay.step(client, CONFIG, payload, service_state=service)
            self.assertTrue(triggered)
            state_index = next(i for i, row in enumerate(client.writes) if row[:2] == (2, 1))
            commit_index = next(i for i, row in enumerate(client.writes) if row[:2] == (kind, 2))
            self.assertLess(state_index, commit_index)
            self.assertEqual(client.state, 1)
            sequences = [row[2] for row in client.writes]
            self.assertEqual(sequences, list(range(sequences[0], sequences[-1]+1)))

    async def test_catalogue_can_be_preempted_without_duplicate_sequence(self):
        await self.check_preemption(3)

    async def test_question_options_can_be_preempted_without_duplicate_sequence(self):
        await self.check_preemption(6)

    async def test_summary_can_be_preempted_without_duplicate_sequence(self):
        await self.check_preemption(7)


if __name__ == '__main__':
    unittest.main()
