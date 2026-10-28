import importlib.util
import unittest
import hmac
import struct
from pathlib import Path
spec=importlib.util.spec_from_file_location('bridge',Path(__file__).with_name('bridge.py'));bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
class ProtocolTests(unittest.TestCase):
 def setUp(self):
  self.key=bytes(range(32));self.config={'v':1,'device':'Xueqiu-test','key':self.key.hex()};self.info={'v':1,'device':'Xueqiu-test','nonce':'0123456789abcdef','accepted':4,'applied':4,'provider':0,'state':0};self.info['proof']=hmac.digest(self.key,b'XQINFO'+struct.pack('<8sIIBB',bytes.fromhex(self.info['nonce']),4,4,0,0),'sha256').hex()
 def test_wire_contract(self):
  p=bridge.make_packet(self.key,self.info,'Claude','completed');self.assertEqual(len(p),47);self.assertEqual(struct.unpack('<BBBI8s',p[:15]),(1,1,4,5,bytes.fromhex(self.info['nonce'])));self.assertEqual(p[15:],hmac.digest(self.key,p[:15],'sha256'))
 def test_v2_unicode_metadata_and_mac(self):
  title='雪球会话 🐶'; p=bridge.make_packet(self.key,self.info,'Codex','running',session_id='ab'*16,display_name=title)
  self.assertEqual(p[0],2);self.assertEqual(p[15:31],bytes.fromhex('ab'*16));self.assertEqual(p[31],len(title.encode()))
  self.assertEqual(p[32:-32].decode(),title);self.assertEqual(p[-32:],hmac.digest(self.key,p[:-32],'sha256'))
  altered=bytearray(p);altered[32]^=1;self.assertNotEqual(altered[-32:],hmac.digest(self.key,altered[:-32],'sha256'))
 def test_v2_maximum_empty_and_bad_metadata(self):
  p=bridge.make_packet(self.key,self.info,'Codex','idle',session_id='00'*16,display_name='汉'*64);self.assertEqual(len(p),256)
  self.assertEqual(len(bridge.make_packet(self.key,self.info,'Codex','idle',session_id='00'*16)),64)
  for title in ['🐶'*49,'汉'*65,'x\x00','x\n','\ud800','\u202e']:
   with self.subTest(title=repr(title)),self.assertRaises(ValueError):bridge.make_packet(self.key,self.info,'Codex','idle',session_id='00'*16,display_name=title)
  with self.assertRaises(ValueError):bridge.make_packet(self.key,self.info,'Codex','idle',display_name='orphan')
 def test_authenticated_ack(self):self.assertIs(bridge.verify_info(self.info,self.config),self.info)
 def test_modified_ack_rejected(self):
  self.info['applied']=5
  with self.assertRaises(ValueError):bridge.verify_info(self.info,self.config)
 def test_wrong_device_rejected(self):
  self.info['device']='Xueqiu-other'
  with self.assertRaises(ValueError):bridge.verify_info(self.info,self.config)
 def test_unknown_state_rejected(self):
  with self.assertRaises(ValueError):bridge.make_packet(self.key,self.info,'Codex','approve-shell-command')
if __name__=='__main__':unittest.main()
