import hashlib
import hmac
import json
import struct
import unittest
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from power_telemetry import decode, clock_packet, PowerRelay

KEY=b'x'*32
NONCE='0123456789abcdef'
def envelope(values=None, nonce=NONCE):
    values=values or [2,100,1792340000,3900,70,1,1,1,30,70,90,80,1,1,0,2,0,0]
    data={'v':1,'n':nonce,'b':'abcdef01','t':values}
    raw=json.dumps(data,separators=(',',':')).encode()
    return json.dumps({'data':data,'h':hmac.digest(KEY,raw,'sha256').hex()}).encode()

class PowerTests(unittest.TestCase):
    def test_authenticated_estimated_battery(self):
        row=decode(envelope(),KEY,NONCE)
        self.assertEqual(row['percent'],70)
        self.assertTrue(row['estimated'])
        self.assertEqual(row['battery_s'],90)
    def test_tampering_and_previous_boot_nonce_rejected(self):
        with self.assertRaises(ValueError):decode(envelope().replace(b'3900',b'4100'),KEY,NONCE)
        with self.assertRaises(ValueError):decode(envelope(nonce='f'*16),KEY,NONCE)
    def test_unknown_clock_is_not_fabricated(self):
        values=[2,100,0,3900,70,1,1,1,30,70,90,80,2,1,0,0,0,0]
        self.assertIsNone(decode(envelope(values),KEY,NONCE)['timestamp'])
    def test_old_sensor_value_is_invalid(self):
        values=[2,100,0,3900,70,1,1,1,30,70,90,80,2,1,0,0,0,21]
        self.assertFalse(decode(envelope(values),KEY,NONCE)['valid'])
    def test_bogus_voltage_percent_and_boolean_rejected(self):
        for index,value in [(3,12000),(4,101),(7,2),(5,9),(6,8)]:
            values=[2,100,0,3900,70,1,1,1,30,70,90,80,2,1,0,0,0,0]
            values[index]=value
            with self.assertRaises(ValueError):decode(envelope(values),KEY,NONCE)
    def test_clock_packet_is_separate_authenticated_opcode(self):
        data=clock_packet(KEY,{'accepted':8,'nonce':NONCE},1792340000)
        self.assertEqual(struct.unpack('<BBBI8sI',data[:19]),(5,0,0,9,bytes.fromhex(NONCE),1792340000))
        self.assertEqual(data[19:],hmac.digest(KEY,data[:19],'sha256'))
        with self.assertRaises(ValueError):clock_packet(KEY,{'accepted':8,'nonce':NONCE},0)

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_attention_logs_without_renewing_an_ai_task(self):
        sent=[]
        class Client:
            services=SimpleNamespace(get_characteristic=lambda _:object())
            async def write_gatt_char(self,uuid,packet,**kwargs):sent.append(packet)
            async def read_gatt_char(self,uuid):return envelope()
        async def info(client,config):return {'nonce':NONCE,'accepted':8}
        with tempfile.TemporaryDirectory() as temp:
            relay=PowerRelay(temp)
            await relay.step(Client(),{'key':KEY.hex(),'device':'test-device'},info,
                             {'provider':'Codex','task_fresh':False,'status':'attention'},
                             SimpleNamespace(valid_until=time.time()+30))
            row=json.loads((Path(temp)/'latest.json').read_text())
            self.assertFalse(row['task_fresh'])
            self.assertTrue(row['status_current'])
            self.assertTrue(row['received_iso'].endswith('+00:00'))
            self.assertEqual([packet[0] for packet in sent],[5])
            self.assertEqual(len(list(Path(temp).glob('power-*.jsonl'))),1)
    async def test_missing_or_rejected_battery_data_does_not_escape_into_status_loop(self):
        class Client:
            services=SimpleNamespace(get_characteristic=lambda _:object())
            async def write_gatt_char(self,*args,**kwargs):pass
            async def read_gatt_char(self,uuid):return envelope().replace(b'3900',b'4100')
        async def info(client,config):return {'nonce':NONCE,'accepted':8}
        with tempfile.TemporaryDirectory() as temp:
            relay=PowerRelay(temp)
            await relay.step(Client(),{'key':KEY.hex(),'device':'test-device'},info)
            self.assertTrue(relay.failed)
            self.assertFalse((Path(temp)/'latest.json').exists())

if __name__=='__main__':unittest.main()
