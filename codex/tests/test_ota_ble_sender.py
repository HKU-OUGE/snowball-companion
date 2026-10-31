import asyncio,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from ota_ble_sender import *
class Transport:
 def __init__(self):self.seq=0;self.sent=[];self.nonce='00'*8
 async def info(self):return {'ota_v':1,'nonce':self.nonce,'accepted':self.seq}
 async def write(self,p):
  import struct
  _,_,op,seq,_=struct.unpack_from('<BBBI8s',p);assert seq==self.seq+1;self.seq=seq;self.sent.append(op)
 async def ota_status(self):return {'seq':self.seq,'busy':False,'result':0}
def test_sender_uses_acknowledged_packets_and_live_heartbeat():
 async def run():
  t=Transport();calls=[]
  async def live():calls.append(1)
  result=await transfer_release(t,b'x'*32,b'hello',b'i'*400,live,lambda:False)
  assert t.sent==[BEGIN,MANIFEST,IMAGE_BEGIN,IMAGE,IMAGE,IMAGE,FINISH]
  assert len(calls)==len(t.sent) and result['seq']==len(t.sent)
 asyncio.run(run())
def test_activity_prevents_begin_and_unsupported_is_never_written():
 async def run():
  t=Transport()
  async def live():pass
  with pytest.raises(OTAInterrupted):await transfer_release(t,b'x'*32,b'm',b'i',live,lambda:True)
  assert t.sent==[]
 asyncio.run(run())

def test_new_activity_cancels_partial_transfer():
 async def run():
  t=Transport()
  async def live():pass
  with pytest.raises(OTAInterrupted):await transfer_release(t,b'x'*32,b'm',b'i',live,lambda:BEGIN in t.sent)
  assert t.sent==[BEGIN,CANCEL]
 asyncio.run(run())

def test_unexpected_transfer_error_cancels_partial_transfer():
 async def run():
  t=Transport()
  async def live():
   if BEGIN in t.sent and CANCEL not in t.sent:raise RuntimeError('temporary failure')
  # Cleanup itself must not rely on the failed service callback.
  with pytest.raises(RuntimeError):await transfer_release(t,b'x'*32,b'm',b'i',live,lambda:False)
  assert t.sent==[BEGIN,CANCEL]
 asyncio.run(run())
