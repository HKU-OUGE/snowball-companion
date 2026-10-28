import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from bridge import cached_peer,find_device
from speech_relay import SpeechRelay
import user_services

class CachedReconnect(unittest.IsolatedAsyncioTestCase):
    def test_connected_device_does_not_require_advertising_or_service_resolution(self):
        peer={'org.bluez.Device1':{'Name':'Snowball test','Address':'01:02:03:04:05:06','Connected':True}}
        self.assertEqual(cached_peer({'/peer':peer},{'device':'Snowball test'})[0],'/peer')
        peer['org.bluez.Device1'].update(Connected=False,Paired=True)
        self.assertIsNotNone(cached_peer({'/peer':peer},{'device':'Snowball test'}))
        peer['org.bluez.Device1']['Paired']=False
        self.assertIsNone(cached_peer({'/peer':peer},{'device':'Snowball test'}))

    async def test_cached_connection_skips_discovery(self):
        with patch('bridge.cached_linux_device',return_value='bound-peer'),patch('bleak.BleakScanner.discover') as scan:
            self.assertEqual(await find_device({'device':'Snowball test'}),'bound-peer')
        scan.assert_not_called()

    def test_duplicate_cached_names_are_not_silently_selected(self):
        peer={'org.bluez.Device1':{'Name':'Snowball test','Address':'01:02:03:04:05:06','Paired':True}}
        with self.assertRaises(ValueError):cached_peer({'/a':peer,'/b':peer},{'device':'Snowball test'})

    async def test_disconnect_consumes_worker_failure_without_resending(self):
        relay=SpeechRelay(None)
        async def fail():raise TimeoutError('test')
        relay.health_task=asyncio.create_task(fail());await asyncio.sleep(0)
        relay.task=asyncio.create_task(asyncio.sleep(30));relay.job={'phase':'sending'}
        await relay.close()
        self.assertIsNone(relay.job);self.assertIsNone(relay.task)

class LoginService(unittest.TestCase):
    def test_owner_guard_and_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as d,patch.object(user_services,'UNITS',Path(d)):
            p=Path(d)/user_services.unit_name('bridge')
            p.write_text('# other install\n');self.assertFalse(user_services.owned('bridge'))
            with self.assertRaises(ValueError):user_services.control('bridge','stop')
            p.write_text(user_services.contents('bridge',Path('/tmp/runtime with space/python')))
            self.assertTrue(user_services.owned('bridge'))
            with patch('user_services.subprocess.run') as run:user_services.control('bridge','stop')
            self.assertEqual(run.call_args.args[0][-2:],['stop','snowball-bridge.service'])
            self.assertIn('Restart=on-failure',p.read_text());self.assertIn('WantedBy=default.target',p.read_text())
            self.assertIn('"/tmp/runtime with space/python"',p.read_text())
            self.assertIn('\nWorkingDirectory=/'+str(user_services.ROOT).lstrip('/'),p.read_text())
            self.assertNotIn('WorkingDirectory="',p.read_text())

if __name__=='__main__':unittest.main()
