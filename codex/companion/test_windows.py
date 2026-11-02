import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import AsyncMock, patch

import bridge
from windows_supervisor import child_arguments

class WindowsReconnect(unittest.IsolatedAsyncioTestCase):
    async def test_paired_peer_bypasses_public_scan_and_closes_handle(self):
        peer = types.SimpleNamespace(bluetooth_address=0x010203040506, close=lambda: closed.append(True))
        closed=[]
        devices=types.SimpleNamespace(
            get_device_selector_from_pairing_state=lambda paired:'paired-only',
            from_id_async=AsyncMock(return_value=peer))
        info=types.SimpleNamespace(find_all_async_aqs_filter=AsyncMock(return_value=[types.SimpleNamespace(name='Snowball test',id='test-id')]))
        modules={'winrt.windows.devices.bluetooth':types.SimpleNamespace(BluetoothLEDevice=devices),
                 'winrt.windows.devices.enumeration':types.SimpleNamespace(DeviceInformation=info)}
        with patch.dict(sys.modules,modules), patch.object(bridge.sys,'platform','win32'), patch('bleak.BleakScanner.discover') as scan:
            result=await bridge.find_device({'device':'Snowball test'})
        self.assertEqual(result.address,'01:02:03:04:05:06')
        self.assertEqual(closed,[True]);scan.assert_not_called()
        info.find_all_async_aqs_filter.assert_awaited_once_with('paired-only')

    async def test_duplicate_paired_names_fail_closed(self):
        devices=types.SimpleNamespace(get_device_selector_from_pairing_state=lambda paired:'paired-only')
        info=types.SimpleNamespace(find_all_async_aqs_filter=AsyncMock(return_value=[types.SimpleNamespace(name='Snowball test')]*2))
        with patch.dict(sys.modules,{'winrt.windows.devices.bluetooth':types.SimpleNamespace(BluetoothLEDevice=devices), 'winrt.windows.devices.enumeration':types.SimpleNamespace(DeviceInformation=info)}), patch.object(bridge.sys,'platform','win32'):
            with self.assertRaises(ValueError):await bridge.cached_windows_device({'device':'Snowball test'})

    async def test_usb_binding_uses_cdc_dtr_and_crlf_without_logging_key(self):
        # No real USB or production credentials are used.
        import contextlib,io
        key='aa'*32
        class Serial:
            def __init__(self):self.lines=[b'XQPAIR '+json.dumps({'v':1,'device':'Snowball test','key':key}).encode()+b'\n'];self.sent=[];instances.append(self)
            def open(self):pass
            def read(self,size):return b''
            def write(self,data):self.sent.append(data)
            def flush(self):pass
            def readline(self):return self.lines.pop(0)
            def close(self):self.closed=True
        instances=[]
        ticks=iter([0,5,6,7])
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules,{'serial':types.SimpleNamespace(Serial=Serial)}),patch.object(bridge.sys,'platform','win32'),patch('bridge.time.monotonic',side_effect=lambda:next(ticks)):
            output=io.StringIO()
            with contextlib.redirect_stdout(output):bridge.pair_usb('TEST-PORT',Path(folder)/'pair.json')
            self.assertEqual(json.loads((Path(folder)/'pair.json').read_text())['key'],key)
        self.assertTrue(instances[0].dtr);self.assertFalse(instances[0].rts)
        self.assertEqual(instances[0].sent,[b':PAIR\r\n']);self.assertTrue(instances[0].closed)
        self.assertNotIn(key,output.getvalue())

    async def test_usb_binding_reports_expired_pairing_window_without_saving(self):
        from unittest.mock import Mock
        serial=Mock()
        serial.readline.return_value=b'boot log XQPAIR_ERROR open_connection_page\r\n'
        ticks=iter([0,5,6,7])
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules,{'serial':types.SimpleNamespace(Serial=lambda:serial)}),patch('bridge.time.monotonic',side_effect=lambda:next(ticks)):
            target=Path(folder)/'pair.json'
            with self.assertRaisesRegex(RuntimeError,'Enable Start pairing'):
                bridge.pair_usb('TEST-PORT',target)
            self.assertFalse(target.exists())
        serial.close.assert_called_once()

class SupervisorTests(unittest.TestCase):
    def test_bridge_always_uses_real_expiring_hub_source(self):
        args=child_arguments('bridge',Path('private-state'))
        self.assertEqual(args,['watch','--source',str(Path('private-state/snapshot.json'))])
        self.assertEqual(child_arguments('status_hub'),[])
        with self.assertRaises(ValueError):child_arguments('demo')

@unittest.skipUnless(sys.platform=='win32','Native Windows locking test')
class WindowsLocks(unittest.TestCase):
    def test_other_process_cannot_lock_until_owner_exits(self):
        import windows_file_lock as locks
        helper=Path(locks.__file__).resolve()
        code='import sys;sys.path.insert(0,sys.argv[1]);import windows_file_lock as f;h=open(sys.argv[2],"a+b");f.flock(h,f.LOCK_EX|f.LOCK_NB)'
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'lock'
            with open(path,'a+b') as handle:
                locks.flock(handle,locks.LOCK_EX|locks.LOCK_NB)
                child=subprocess.run([sys.executable,'-c',code,str(helper.parent),str(path)],capture_output=True)
                self.assertNotEqual(child.returncode,0)
                self.assertIn(b'BlockingIOError',child.stderr)
            child=subprocess.run([sys.executable,'-c',code,str(helper.parent),str(path)],capture_output=True)
            self.assertEqual(child.returncode,0,child.stderr.decode())

if __name__=='__main__':unittest.main()
