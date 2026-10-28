import asyncio,hmac,importlib.util,json,struct,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
spec=importlib.util.spec_from_file_location('info_bridge',Path(__file__).with_name('bridge.py'));bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
from pet_sync import verify_pet_info
class InfoTransportTests(unittest.TestCase):
 def fixture(self):
  key=bytes(range(32));nonce=bytes(range(8));config={'device':'Xueqiu-test','key':key.hex()}
  a=d=rq=rg=ps=0xffffffff;rid=bytes.fromhex('ab'*16);before=bytes.fromhex('cd'*16);pet='p'*31
  h=hmac.digest(key,b'XQINFO'+struct.pack('<8sIIBB',nonce,a,d,2,5),'sha256').hex()
  rh=hmac.digest(key,struct.pack('<8sIBIB16s',nonce,rq,2,rg,True,rid)+bytes([1])+before,'sha256').hex()
  ph=hmac.digest(key,b'XQPET'+nonce+struct.pack('<IBB',ps,3,len(pet))+pet.encode(),'sha256').hex()
  return config,{'v':2,'d':config['device'],'n':nonce.hex(),'a':a,'s':[d,2,5],'h':h,'r':[rq,2,rg,True,rid.hex(),1,before.hex(),rh],'p':[ps,3,pet,ph]}
 def test_compact_preserves_three_authenticated_messages(self):
  config,wire=self.fixture();raw=json.dumps(wire,separators=(',',':')).encode();self.assertLessEqual(len(raw),512)
  info=bridge.verify_info(bridge.decode_info(raw),config)
  self.assertIsNotNone(bridge.verify_request(info,config));self.assertEqual(verify_pet_info(info,bytes.fromhex(config['key']))['character_id'],'p'*31)
 def test_old_v1_supported(self):
  config,wire=self.fixture();old=bridge.decode_info(json.dumps(wire));self.assertEqual(bridge.verify_info(bridge.decode_info(json.dumps(old)),config),old)
 def test_all_authentication_proofs_remain_required(self):
  config,wire=self.fixture()
  for group in ['h','r','p']:
   bad=json.loads(json.dumps(wire))
   if group=='h':bad[group]='0'*64
   else:bad[group][-1]='0'*64
   info=bridge.decode_info(json.dumps(bad))
   with self.assertRaises(ValueError):
    bridge.verify_info(info,config);bridge.verify_request(info,config);verify_pet_info(info,bytes.fromhex(config['key']))
 def test_truncation_and_malformed_schema_are_retryable(self):
  _,wire=self.fixture();raw=json.dumps(wire).encode()
  for bad in [raw[:100],raw[:-1],b'\xff',b'[]',b'{"v":2,"s":[]}']:
   with self.assertRaises(bridge.InfoTransportError):bridge.decode_info(bad)
 def test_watch_does_not_retry_authentication_failure(self):
  async def exercise():
   config,_=self.fixture()
   with tempfile.TemporaryDirectory() as d:
    path=Path(d)/'test.json';path.write_text(json.dumps(config));args=SimpleNamespace(command='watch',config=path)
    with patch.object(bridge,'connected_session',AsyncMock(side_effect=ValueError('Authentication failed'))) as connect,patch.object(bridge.asyncio,'sleep',AsyncMock()) as sleep:
     with self.assertRaises(ValueError):await bridge.run(args)
     self.assertEqual(connect.await_count,1);sleep.assert_not_awaited()
  asyncio.run(exercise())
 def test_watch_reconnects_after_truncated_info(self):
  async def exercise():
   config,wire=self.fixture()
   with tempfile.TemporaryDirectory() as d:
    path=Path(d)/'test.json';path.write_text(json.dumps(config));args=SimpleNamespace(command='watch',config=path)
    client=SimpleNamespace(read_gatt_char=AsyncMock(return_value=b'{"v":2'))
    async def bad():await bridge.read_info(client,config)
    with self.assertRaises(bridge.InfoTransportError):await bad()
    with patch.object(bridge,'connected_session',AsyncMock(side_effect=[bridge.InfoTransportError('truncated'),None])) as connect,patch.object(bridge.asyncio,'sleep',AsyncMock()) as sleep:
     await bridge.run(args);self.assertEqual(connect.await_count,2);sleep.assert_awaited_once_with(2)
  asyncio.run(exercise())
if __name__=='__main__':unittest.main()
