"""Offline synthetic fixtures only; no radio, service, real profile or HTTP."""
import asyncio
import hmac
import importlib.util
import json
import os
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import connection_diagnostics as diag
import pairing_store
import platform_support as platform

CONFIG = {'v': 1, 'device': 'Xueqiu-01020304', 'key': 'ab' * 32}


def info_payload():
    info = {'v': 1, 'device': CONFIG['device'], 'nonce': '0123456789abcdef',
            'accepted': 4, 'applied': 4, 'provider': 0, 'state': 0}
    raw = b'XQINFO' + struct.pack('<8sIIBB', bytes.fromhex(info['nonce']), 4, 4, 0, 0)
    info['proof'] = hmac.digest(bytes.fromhex(CONFIG['key']), raw, 'sha256').hex()
    return info


class Client:
    def __init__(self):
        self.is_connected = False
        self.disconnect_calls = 0
        self.info = info_payload()
        self.connect_error = None
        self.disconnect_error = False
    async def connect(self):
        if self.connect_error:
            raise self.connect_error
        self.is_connected = True
    async def disconnect(self):
        self.disconnect_calls += 1
        if self.disconnect_error:
            raise OSError('synthetic-sensitive-error')
        self.is_connected = False
    async def read_gatt_char(self, characteristic):
        return json.dumps(self.info).encode()


class ConnectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.client = Client()
        for name, replacement in (
                ('background_status', lambda: {'ok': True, 'code': 'stopped'}),
                ('_discover', AsyncMock(return_value=object())),
                ('_client', lambda device: self.client)):
            patcher = patch.object(diag, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def check(self):
        return await diag.check_connection(CONFIG, profile=self.profile)

    async def test_real_hmac_auth_and_explicit_disconnect(self):
        result = await self.check()
        self.assertTrue(result['ok'], result)
        self.assertTrue(result['disconnected'])
        self.assertEqual(self.client.disconnect_calls, 1)
        output = json.dumps(result)
        for secret in (CONFIG['key'], CONFIG['device'], self.client.info['nonce'], self.client.info['proof']):
            self.assertNotIn(secret, output)

    async def test_auth_mismatch_disconnects(self):
        self.client.info['proof'] = '00' * 32
        result = await self.check()
        self.assertEqual(result['code'], 'auth_mismatch')
        self.assertEqual(self.client.disconnect_calls, 1)

    async def test_incomplete_info_is_gatt_not_auth(self):
        async def broken(characteristic):
            return b'{'
        self.client.read_gatt_char = broken
        self.assertEqual((await self.check())['code'], 'gatt_error')
        self.assertEqual(self.client.disconnect_calls, 1)

    async def test_timeout_not_claimed_occupied_and_connect_failure_disconnects(self):
        self.client.connect_error = TimeoutError('synthetic secret')
        result = await self.check()
        self.assertEqual(result['code'], 'timeout')
        self.assertEqual(self.client.disconnect_calls, 1)
        self.assertNotIn('synthetic secret', json.dumps(result))

    async def test_explicit_busy_backend_identifier(self):
        error = OSError('arbitrary detail')
        error.dbus_error = 'org.bluez.Error.InProgress'
        self.client.connect_error = error
        self.assertEqual((await self.check())['code'], 'occupied')

    async def test_disconnect_failure_not_success(self):
        self.client.disconnect_error = True
        result = await self.check()
        self.assertEqual(result['code'], 'disconnect_failed')
        self.assertFalse(result['disconnected'])

    async def test_cancel_also_explicitly_disconnects(self):
        entered = asyncio.Event()
        async def blocked(client, config):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(diag, '_authenticated_info', blocked):
            task = asyncio.create_task(self.check())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.client.disconnect_calls, 1)
        with diag.device_session_lock(self.profile):
            pass

    async def test_background_running_refuses_before_scan(self):
        with patch.object(diag, 'background_status', return_value=diag.result('background_running')), \
             patch.object(diag, '_discover', AsyncMock()) as scan:
            self.assertEqual((await self.check())['code'], 'background_running')
            scan.assert_not_awaited()

    async def test_lock_contention_refuses_and_recovers(self):
        with diag.device_session_lock(self.profile):
            self.assertEqual((await self.check())['code'], 'session_busy')
        self.assertTrue((await self.check())['ok'])

    async def test_device_missing_vs_adapter_missing(self):
        class BleakBluetoothNotAvailableError(Exception):
            reason = 'NO_BLUETOOTH'
        for error, code in [(diag.DiagnosticError('device_not_found'), 'device_not_found'),
                            (BleakBluetoothNotAvailableError('secret'), 'no_adapter')]:
            with patch.object(diag, '_discover', AsyncMock(side_effect=error)):
                self.assertEqual((await self.check())['code'], code)
        self.assertEqual(self.client.disconnect_calls, 0)


class DoctorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name)
        self.pairing = self.profile / '.config/xueqiu/ble-pairing.json'
        pairing_store.save(CONFIG, self.pairing)
        for name, replacement in (
                ('background_status', lambda: diag.result('background_running')),
                ('_adapter_status', AsyncMock(return_value={'ok': True, 'code': 'adapter_present', 'message': 'test'})),
                ('_hub_health', lambda profile: {'ok': True, 'code': 'hub_healthy', 'message': 'test'})):
            patcher = patch.object(diag, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def write(self, relative, value):
        path = self.profile / relative
        platform.ensure_private_dir(path.parent)
        path.write_text(json.dumps(value))
        platform.protect_private(path)
        return path

    async def doctor(self):
        return await diag.doctor(self.pairing, profile=self.profile)

    async def test_real_process_hook_receipt_seen_in_default_path(self):
        path = Path(__file__).resolve().parent.parent / 'plugins/xueqiu-status/scripts/codex_hook.py'
        spec = importlib.util.spec_from_file_location('synthetic_codex_hook_for_doctor', path)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        emitted = []
        with patch.object(hook, 'session_title', return_value=None):
            hook.process_hook({'hook_event_name': 'UserPromptSubmit', 'session_id': 'synthetic-only',
                               'prompt': 'PRIVATE_SYNTHETIC_PROMPT'},
                              self.profile / '.local/state/xueqiu/codex-adapter', emitted.append)
        result = await self.doctor()
        receipt = next(item for item in result['checks'] if item['code'] == 'hooks_recent')
        self.assertTrue(receipt['ok'])
        self.assertEqual(len(emitted), 1)
        text = json.dumps(result)
        for private in (CONFIG['key'], CONFIG['device'], 'synthetic-only', 'PRIVATE_SYNTHETIC_PROMPT'):
            self.assertNotIn(private, text)

    async def test_mtime_or_unconfirmed_hook_not_delivery(self):
        self.write('.local/state/xueqiu/codex-adapter/hooks/test.json',
                   {'last_delivery': {'at': time.time(), 'status': 'unconfirmed'}})
        result = await self.doctor()
        self.assertTrue(any(item['code'] == 'hooks_no_recent_delivery' for item in result['checks']))

    async def test_running_background_is_healthy_and_no_active_radio(self):
        with patch.object(diag, '_discover', AsyncMock()) as scan, \
             patch.object(diag, 'check_connection', AsyncMock()) as connect:
            result = await self.doctor()
        self.assertTrue(next(row for row in result['checks'] if row['code'] == 'background_running')['ok'])
        scan.assert_not_awaited()
        connect.assert_not_awaited()

    async def test_stopped_with_binding_is_runtime_failure(self):
        with patch.object(diag, 'background_status', return_value={'ok': True, 'code': 'stopped'}):
            result = await self.doctor()
        self.assertFalse(next(row for row in result['checks'] if row['code'] == 'stopped')['ok'])

    async def test_safe_transport_record_specific_error(self):
        self.assertTrue(diag.record_transport('error', error='auth_mismatch', profile=self.profile))
        result = await self.doctor()
        self.assertTrue(any(row['code'] == 'auth_mismatch' for row in result['checks']))
        self.assertFalse(diag.record_transport('error', error=CONFIG['key'], profile=self.profile))
        self.assertFalse(diag.record_transport('applied', ack=True, profile=self.profile))
        self.assertFalse(diag.record_transport('applied', ack=2**32, profile=self.profile))

    async def test_record_io_error_never_interrupts_transport(self):
        with patch.object(diag.platform, 'ensure_private_dir', side_effect=OSError('secret')):
            self.assertFalse(diag.record_transport('alive', profile=self.profile))

    async def test_plugin_receipt_not_trust_claim_and_windows_limit_neutral(self):
        self.write('.local/state/xueqiu/windows-plugin.json',
                   {'schema': 1, 'version': '0.7.0', 'registered': True, 'hooks_require_review': True,
                    'untrusted_detail': CONFIG['key']})
        with patch.object(diag.sys, 'platform', 'win32'):
            result = await self.doctor()
        receipt = next(row for row in result['checks'] if row['code'] == 'plugin_receipt')
        self.assertTrue(receipt['hooks_require_review'])
        self.assertIn('不代表', receipt['message'])
        neutral = next(row for row in result['checks'] if row['code'] == 'desktop_control_unsupported')
        self.assertIsNone(neutral['ok'])
        self.assertNotIn(CONFIG['key'], json.dumps(result))


if __name__ == '__main__':
    unittest.main()
