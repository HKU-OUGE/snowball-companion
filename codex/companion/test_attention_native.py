"""Compile actual question parser and UTF-8 validator, including fragmented text."""
import ctypes
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from attention_relay import question_packets

class NativeAttentionTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name);native=Path(__file__).parents[1]/'main'
  source=(native/'xueqiu_ble.c').read_text();validator=source[source.index('static bool valid_display_text('):source.index('static bool valid_title(')]
  code=f'#include "{native.resolve()}/attention_wire.h"\n'+validator+'''
static xqa_stage_t stage;static xqa_list_t shown;static int64_t clock_us=100;
static bool valid(const uint8_t*p,size_t n){return valid_display_text(p,n,256);}
int push(unsigned p,unsigned op,const uint8_t*v,unsigned n){int r=xqa_receive(&stage,p,op,v,n,clock_us,valid);if(r==1)shown=stage.list;return r;}
void reset(void){memset(&stage,0,sizeof(stage));memset(&shown,0,sizeof(shown));clock_us=100;}
void advance(void){clock_us+=6000000;}
const char *text(void){return shown.rows[0].text;}
int count(void){return shown.count;}
'''
  (root/'test.c').write_text(code);subprocess.run(['cc','-shared','-fPIC','-Wall','-Werror',str(root/'test.c'),'-o',str(root/'test.so')],check=True,capture_output=True)
  cls.lib=ctypes.CDLL(str(root/'test.so'));cls.lib.push.argtypes=[ctypes.c_uint,ctypes.c_uint,ctypes.c_char_p,ctypes.c_uint];cls.lib.text.restype=ctypes.c_char_p
 @classmethod
 def tearDownClass(cls):cls.temp.cleanup()
 def setUp(self):self.lib.reset();self.row={'id':'1'*32,'session_wire':'2'*32,'text':'问题里的文字可以完整滚动。'*15,'status':'active'}
 def push(self,op,body,p=0):return self.lib.push(p,op,body,len(body))
 def test_only_complete_transaction_visible(self):
  packets=list(question_packets(9,[self.row],1));self.assertEqual(self.push(*packets[0]),0);self.assertEqual(self.lib.count(),0)
  self.assertEqual(self.push(2,struct.pack('<I',9)),-1)
  for packet in packets[1:-1]:self.assertEqual(self.push(*packet),0);self.assertEqual(self.lib.count(),0)
  self.assertEqual(self.push(*packets[-1]),1);self.assertEqual(self.lib.text().decode(),self.row['text'])
 def test_wrong_generation_provider_and_duplicate_chunk_rejected(self):
  packets=list(question_packets(9,[self.row],1));self.push(*packets[0]);op,body=packets[1]
  self.assertEqual(self.push(op,body,p=1),-1);self.assertEqual(self.push(op,b'\x08'+body[1:]),-1)
  self.assertEqual(self.push(op,body),0);self.assertEqual(self.push(op,body),-1)
 def test_expired_stage_cannot_publish(self):
  packets=list(question_packets(9,[self.row],1));self.push(*packets[0]);self.lib.advance();self.assertEqual(self.push(*packets[1]),-1)
 def test_malformed_utf8_controls_and_oversize_rejected(self):
  for text in ['a\n','\u202e','问'*257]:
   self.lib.reset();row={**self.row,'text':text};results=[self.push(op,b) for op,b in question_packets(9,[row],1)]
   self.assertIn(-1,results);self.assertEqual(self.lib.count(),0)
 def test_empty_commit_clears_old_questions(self):
  for op,b in question_packets(9,[self.row],1):self.push(op,b)
  for op,b in question_packets(10,[],0):self.push(op,b)
  self.assertEqual(self.lib.count(),0)
