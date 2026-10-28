"""OTA quiet-mode tests use signed fixtures, never radio or local services."""
import hmac
from contextlib import ExitStack
import json
import struct
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import network_provision as np
import bridge

KEY = bytes(range(32))
NONCE = '1122334455667788'
PAIR = {'key': KEY.hex(), 'device': 'Fixture'}
INFO = {'network_v': 1, 'accepted': 7, 'nonce': NONCE, 'device': 'Fixture'}
POWER = '9e7a0004-6ed4-4b6b-bf37-43dc42838c20'


class Client:
    def __init__(self, direct=True):
        self.direct = direct
        self.services = self
        self.reads, self.writes = [], []
        self.status = {'v': 1, 'active': False, 'state': 'updating', 'window': False}
        self.bad_signature = False

    def get_characteristic(self, uuid):
        return uuid if self.direct and uuid == np.NETWORK else None

    async def read_gatt_char(self, uuid):
        self.reads.append(uuid)
        raw = json.dumps(self.status, separators=(',', ':'), ensure_ascii=False).encode()
        proof = hmac.digest(KEY, b'XQNETSTATUS' + bytes.fromhex(NONCE) + raw, 'sha256').hex()
        return json.dumps({'data': self.status, 'h': '00' * 32 if self.bad_signature else proof}).encode()

    async def write_gatt_char(self, uuid, raw, response):
        self.writes.append((uuid, bytes(raw), response))


class QuietMode(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = 10.0
        self.info_reads = 0
        self.requests = []
        self.client = Client()
        self.worker = np.NetworkProvision()
        self.timer = patch.object(np, 'time', SimpleNamespace(monotonic=lambda: self.now))
        self.timer.start()
        self.addCleanup(self.timer.stop)

    async def info(self, client, config):
        self.info_reads += 1
        return dict(INFO)

    def control(self, path, data):
        self.requests.append((path, data))
        return {}

    async def step(self):
        return await self.worker.step(self.client, PAIR, self.info)

    async def test_signed_updating_quiets_with_active_false_and_survives_throttle(self):
        with patch.object(np, 'control', self.control):
            self.assertTrue(await self.step())
            self.assertFalse(self.worker.prefer_wifi)
            self.assertTrue(self.worker.maintenance)
            self.now += 1.9
            self.assertTrue(await self.step())
            self.assertEqual(self.info_reads, 1)
            self.assertEqual(self.client.reads, [np.NETWORK])
            self.assertEqual(len(self.requests), 1)
            self.now += .11
            self.assertTrue(await self.step())
            self.assertEqual(self.info_reads, 2)
            self.assertEqual(self.client.reads, [np.NETWORK, np.NETWORK])
            self.assertEqual([path for path, _ in self.requests], ['/v1/bridge', '/v1/bridge'])
            self.assertEqual(self.client.writes, [])

    async def test_local_control_failure_does_not_resume_gatt_traffic(self):
        with patch.object(np, 'control', side_effect=OSError('fixture unavailable')):
            self.assertTrue(await self.step())
            self.assertTrue(self.worker.maintenance)
            self.now += 1
            self.assertTrue(await self.step())
        self.assertEqual(self.client.writes, [])

    async def test_invalid_signature_cannot_enter_or_leave_quiet_mode(self):
        for initial in (False, True):
            self.worker.maintenance = initial
            self.client.bad_signature = True
            self.client.status['state'] = 'ble_priority' if initial else 'updating'
            self.now += 3
            with patch.object(np, 'control', self.control), self.assertRaises(ValueError):
                await self.step()
            self.assertEqual(self.worker.maintenance, initial)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.client.writes, [])

    async def test_signed_normal_state_resumes_and_active_wifi_still_quiets(self):
        with patch.object(np, 'control', self.control):
            self.assertTrue(await self.step())
            self.now += 2.1
            self.client.status['state'] = 'ble_priority'
            self.assertFalse(await self.step())
            self.assertFalse(self.worker.maintenance)
            self.now += .5
            self.assertFalse(await self.step())
            self.now += 2
            self.client.status.update(state='online', active=True)
            self.assertTrue(await self.step())
            self.assertTrue(self.worker.prefer_wifi)
            self.assertFalse(self.worker.maintenance)

    async def test_unexpected_provision_job_fails_clears_and_never_writes_config(self):
        job = {'config': {'device': 'Fixture', 'password': 'fixture-only'}}
        self.client.status['window'] = True
        def control(path, data):
            self.requests.append((path, data))
            return job if path == '/v1/bridge' else {}
        with patch.object(np, 'control', control), patch.object(np, 'seal_config') as seal, patch.object(np, 'packets') as packets:
            self.assertTrue(await self.step())
            seal.assert_not_called()
            packets.assert_not_called()
        self.assertEqual(job, {})
        self.assertEqual(self.client.writes, [])
        self.assertEqual([path for path, _ in self.requests], ['/v1/bridge', '/v1/result'])
        self.assertEqual(self.requests[-1][1]['result'], 'failed')
        self.assertEqual(self.requests[-1][1]['status']['state'], 'updating')

    async def test_old_bluez_cache_uses_only_signed_metadata_selector(self):
        self.client.direct = False
        with patch.object(np, 'control', self.control):
            self.assertTrue(await self.step())
        self.assertEqual(self.info_reads, 1)
        self.assertEqual(self.client.reads, [POWER])
        self.assertEqual(len(self.client.writes), 1)
        uuid, packet, response = self.client.writes[0]
        self.assertEqual(uuid, '9e7a0003-6ed4-4b6b-bf37-43dc42838c20')
        self.assertTrue(response)
        self.assertEqual(packet[:3], bytes([9, 0, 3]))
        self.assertEqual(len(packet), 47)
        self.assertEqual(struct.unpack_from('<I', packet, 3)[0], INFO['accepted'] + 1)
        self.assertTrue(hmac.compare_digest(packet[-32:], hmac.digest(KEY, packet[:-32], 'sha256')))

    async def test_failed_job_rejection_report_keeps_ble_quiet_and_clears_job(self):
        for error in (OSError('fixture unavailable'), ValueError('fixture malformed')):
            self.now += 3
            job = {'config': {'device': 'Fixture', 'password': 'fixture-only'}}
            def control(path, data):
                if path == '/v1/bridge':
                    return job
                raise error
            with patch.object(np, 'control', control), patch.object(np, 'seal_config') as seal:
                self.assertTrue(await self.step())
                self.assertTrue(self.worker.maintenance)
                seal.assert_not_called()
            self.assertEqual(job, {})
            self.assertEqual(self.client.writes, [])

    async def test_normal_provision_result_failure_still_propagates(self):
        self.client.status['state'] = 'ble_priority'
        job = {'config': {'device': 'Fixture'}}
        def control(path, data):
            if path == '/v1/bridge':
                return job
            raise OSError('fixture unavailable')
        with patch.object(np, 'control', control), self.assertRaises(OSError):
            await self.step()
        self.assertEqual(job, {})
        self.assertFalse(self.worker.maintenance)

    async def test_real_bridge_loop_throttles_quiet_reads_and_resumes_normal_relays(self):
        args = SimpleNamespace(command='watch', source=None, provider='Codex', state='idle', quit=False)
        client = self.client
        class Connection:
            async def __aenter__(self):
                return client
            async def __aexit__(self, *_):
                pass
        relays = [Mock(step=AsyncMock(return_value=None)) for _ in range(6)]
        speech = Mock(latency_sensitive=False, transferring=False, step=AsyncMock(), close=AsyncMock())
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            self.now += delay
            if delay == .5:
                for relay in relays:
                    relay.step.assert_not_awaited()
                speech.step.assert_not_awaited()
                if len(sleeps) == 6:
                    client.status['state'] = 'ble_priority'
            elif delay == .2:
                args.quit = True
            self.assertLessEqual(len(sleeps), 9)
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            args.source = Path(directory) / 'fixture-snapshot.json'
            args.source.write_text('{"marker":"fixture"}')
            stack.enter_context(patch('bleak.BleakClient', return_value=Connection()))
            stack.enter_context(patch.object(bridge, 'find_device', AsyncMock(return_value=object())))
            stack.enter_context(patch.object(bridge, 'linux_agent', AsyncMock(return_value=None)))
            stack.enter_context(patch.object(bridge, 'read_info', self.info))
            stack.enter_context(patch.object(bridge, 'parse_source', return_value=bridge.SourceStatus('Codex', 'idle', 'hub', None)))
            stack.enter_context(patch.object(bridge, 'asyncio', SimpleNamespace(sleep=sleep)))
            stack.enter_context(patch.object(np, 'control', self.control))
            for name, relay in zip(('bridge.StatusRelay', 'bridge.CatalogRelay', 'attention_relay.AttentionRelay',
                                     'attention_relay.SummaryRelay', 'pet_sync.PetRelay', 'power_telemetry.PowerRelay'), relays):
                stack.enter_context(patch(name, return_value=relay))
            stack.enter_context(patch('speech_relay.SpeechRelay', return_value=speech))
            await bridge.connected_session(args, PAIR)
        self.assertEqual(sleeps, [.5] * 8 + [.2])
        self.assertEqual(self.info_reads, 4)  # Initial auth + t=10,12,14 metadata.
        self.assertEqual(client.reads, [np.NETWORK] * 3)
        self.assertEqual(client.writes, [])
        for relay in relays:
            relay.step.assert_awaited_once()
        speech.step.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
