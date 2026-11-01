import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import device_setup as setup

CONFIG = {'v': 1, 'device': 'Xueqiu-01020304', 'key': 'ab' * 32}


class SerialDouble:
    def __init__(self, lines):
        self.lines = iter(lines)
        self.calls = []
    def open(self):
        self.calls.append(('open', self.port, self.dtr, self.rts))
    def reset_input_buffer(self):
        self.calls.append(('reset_input_buffer',))
    def write(self, data):
        self.calls.append(('write', data))
    def flush(self):
        pass
    def readline(self, limit):
        return next(self.lines, b'')
    def close(self):
        self.calls.append(('close',))


class SetupTests(unittest.TestCase):
    def serial(self, lines):
        stream = SerialDouble(lines)
        context = patch.dict('sys.modules', {'serial': SimpleNamespace(Serial=lambda: stream)})
        context.start()
        self.addCleanup(context.stop)
        return stream

    def test_explicit_selected_port_and_dtr_rts_before_open(self):
        stream = self.serial([b'boot log\n', b'XQPAIR ' + json.dumps(CONFIG).encode() + b'\n'])
        self.assertEqual(setup.read_pairing('COM9'), CONFIG)
        self.assertEqual(stream.calls[0], ('open', 'COM9', False, False))
        self.assertEqual([call for call in stream.calls if call[0] == 'write'], [('write', b':PAIR\n')])
        self.assertEqual(stream.calls[-1], ('close',))

    def test_physical_page_rejection_not_bypassed(self):
        stream = self.serial([b'XQPAIR_ERROR open_connection_page\n'])
        with self.assertRaisesRegex(setup.SetupError, '^connection_page_required$'):
            setup.read_pairing('COM9')
        self.assertEqual([call for call in stream.calls if call[0] == 'write'], [('write', b':PAIR\n')])
        self.assertEqual(stream.calls[-1], ('close',))

    def test_no_implicit_serial_choice(self):
        stream = self.serial([])
        with self.assertRaisesRegex(setup.SetupError, '^serial_selection_required$'):
            setup.read_pairing('')
        self.assertEqual(stream.calls, [])

    def test_malformed_reply_is_private_error(self):
        self.serial([b'XQPAIR {"key":"DO_NOT_EXPOSE"}\n'])
        with self.assertRaisesRegex(setup.SetupError, '^invalid_credentials$'):
            setup.read_pairing('COM9')

    def test_serial_timeout_is_not_occupied(self):
        stream = self.serial([])
        with patch.object(setup.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(setup.SetupError, '^serial_timeout$'):
                setup.read_pairing('COM9', timeout=1)
        self.assertEqual(stream.calls[-1], ('close',))

    def test_enumeration_metadata_no_autoselect(self):
        item = SimpleNamespace(device='COM7', description='USB Serial', vid=0x303a,
                               pid=0x1001, serial_number='synthetic', manufacturer='Test', product='Test', location='1')
        with patch.dict('sys.modules', {'serial.tools': SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: [item]))}):
            ports = setup.enumerate_ports()
        self.assertEqual(ports[0]['port'], 'COM7')
        self.assertEqual(ports[0]['vid'], 0x303a)


class BindTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_auth_preserves_previous_binding(self):
        with patch.object(setup.diagnostics, 'background_status', return_value={'ok': True}), \
             patch.object(setup, 'read_pairing', return_value=CONFIG), \
             patch.object(setup.diagnostics, 'check_connection', AsyncMock(return_value={'ok': False, 'code': 'auth_mismatch'})), \
             patch.object(setup.pairing_store, 'save') as save:
            self.assertEqual((await setup.bind_device('COM7', 'unused'))['code'], 'auth_mismatch')
            save.assert_not_called()

    async def test_success_saves_only_after_auth_and_redacts(self):
        events = []
        async def checked(config):
            events.append('auth')
            return {'ok': True}
        with patch.object(setup.diagnostics, 'background_status', return_value={'ok': True}), \
             patch.object(setup, 'read_pairing', return_value=CONFIG), \
             patch.object(setup.diagnostics, 'check_connection', checked), \
             patch.object(setup.pairing_store, 'save', side_effect=lambda *args: events.append('save')):
            result = await setup.bind_device('COM7', 'unused')
        self.assertEqual(events, ['auth', 'save'])
        self.assertTrue(result['ok'])
        self.assertNotIn(CONFIG['key'], json.dumps(result))
        self.assertNotIn(CONFIG['device'], json.dumps(result))

    async def test_running_bridge_prevents_serial_read(self):
        with patch.object(setup.diagnostics, 'background_status', return_value={'ok': False, 'code': 'background_running'}), \
             patch.object(setup, 'read_pairing') as serial:
            self.assertEqual((await setup.bind_device('COM7'))['code'], 'background_running')
            serial.assert_not_called()


if __name__ == '__main__':
    unittest.main()
