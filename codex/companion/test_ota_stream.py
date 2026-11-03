import asyncio,hmac,struct
import pytest
from ota_ble_sender import transfer_release,OTAInterrupted
from ota_ble_frames import *
from ota_idle import update_power_allowed
KEY=b'k'*32;NONCE=b'n'*8
class Receiver:
 def __init__(self,window=8):
  self.seq=0;self.window=window;self.streaming=False;self.pending=[];self.data=bytearray();self.ops=[];self.max_pending=0;self.closed=False
 async def info(self):return dict(ota_v=1,nonce=NONCE.hex(),accepted=self.seq,ota_window=self.window)
 async def start_acknowledgements(self):return True
 async def stop_acknowledgements(self):self.closed=True
 def image_window(self,info):return self.window
 async def write(self,p):
  assert hmac.compare_digest(p[-32:],hmac.digest(KEY,p[:-32],'sha256'))
  version,_,op,seq,nonce=struct.unpack_from('<BBBI8s',p)
  assert version==10 and nonce==NONCE and seq==self.seq+1
  self.seq=seq;self.ops.append(op)
  if op==IMAGE:
   assert self.streaming==(self.window>1)
   self.pending.append(p[15:-32]);self.max_pending=max(self.max_pending,len(self.pending))
   assert len(self.pending)<=self.window
  elif op==FINISH:assert not self.pending
 async def wait_ack(self,seq,timeout):
  assert seq==self.seq
  for p in self.pending:
   assert struct.unpack_from('<I',p)[0]==len(self.data)
   self.data.extend(p[4:])
  self.pending=[]
  return dict(seq=seq,phase=5 if self.ops[-1]==FINISH else 3,result=0,busy=False,image=len(self.data))
def test_full_size_burst_keeps_exact_bytes_and_yields_between_windows():
 async def run():
  data=(bytes(range(251))*16692)[:4189648];r=Receiver();live=[0]
  async def service():
   live[0]+=1
   if live[0]%64==0:r.seq+=1;return True # independent authenticated heartbeat writer
   return False
  s=await transfer_release(r,KEY,b'signed manifest',data,service,lambda:False)
  assert s['phase']==5 and bytes(r.data)==data and r.max_pending==8 and r.closed
  assert 2500<live[0]<3000
 asyncio.run(run())
def test_old_peer_retains_single_packet_protocol():
 async def run():
  r=Receiver(1)
  async def service():return False
  await transfer_release(r,KEY,b'm',b'x'*2000,service,lambda:False)
  assert len(r.data)==2000 and r.max_pending==1
 asyncio.run(run())
def test_activity_stops_burst_and_never_finishes_partial_image():
 async def run():
  r=Receiver()
  async def service():return False
  with pytest.raises(OTAInterrupted):await transfer_release(r,KEY,b'm',b'x'*2000,service,lambda:len(r.data)>=768)
  assert FINISH not in r.ops and r.ops[-1]==CANCEL and r.closed and not r.streaming
 asyncio.run(run())
@pytest.mark.parametrize('power,percent,allowed',[(1,30,False),(1,31,True),(1,100,True),(1,101,False),(2,0,True),(3,12,True),(0,80,False)])
def test_update_power_external_or_battery(power,percent,allowed):
 row=dict(device='fixture',connected=True,valid=True,power=power,percent=percent,received_at=100)
 assert update_power_allowed(row,'fixture',110)==allowed
 assert not update_power_allowed(row,'fixture',126)
 assert not update_power_allowed(row,'other',110)
 row['valid']=False;assert not update_power_allowed(row,'fixture',110)
