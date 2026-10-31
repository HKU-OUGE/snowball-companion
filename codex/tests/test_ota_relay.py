import asyncio,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
import ota_relay
class FakeGatt:
 def __init__(self,*args):self.key=b'k'*32
 def supported(self):return True
 async def info(self):return {'nonce':'00'*8,'accepted':0,'ota_v':1}
 async def ota_status(self):return {'version':'0.22.5','staged':False}
async def no_op():pass

def test_no_release_background_check_is_rate_limited(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',FakeGatt)
 async def run():
  calls=[];r=ota_relay.OtaRelay(interval=30,clock=lambda:100,download=lambda version:calls.append(version))
  await r.step(None,{},None,no_op,lambda:False)
  for _ in range(20):
   await asyncio.sleep(.01);await r.step(None,{},None,no_op,lambda:False)
   if r.notice=='current':break
  assert calls==['0.22.5'] and r.notice=='current'
  await r.step(None,{},None,no_op,lambda:False);assert len(calls)==1
 asyncio.run(run())

def test_only_completed_device_stage_marks_success(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',FakeGatt)
 async def send(*args,**kwargs):return {'phase':5}
 monkeypatch.setattr(ota_relay,'transfer_release',send)
 async def run():
  r=ota_relay.OtaRelay();r.ready=(b'manifest',b'image','0.22.6')
  await r.step(None,{},None,no_op,lambda:False)
  assert r.staged and r.ready is None and r.notice=='staged'
 asyncio.run(run())
