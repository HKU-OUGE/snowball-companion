import asyncio,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
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

import json,struct,threading
from ota_download import PreparedCatalogue
from ota_ble_frames import HOST_PROGRESS,CATALOG_BEGIN,CATALOG_DATA,CATALOG_END

class Device:
 def __init__(self,operation=1,target='',request=1):
  self.nonce='11'*8;self.sequence=0;self.frames=[];self.live=0;self.catalogue=b''
  self.status={'v':2,'version':'0.24.0','staged':False,'request':request,'operation':operation,'target':target}
 def request(self,operation,target='',request=2):self.status.update(request=request,operation=operation,target=target)
class ManualGatt:
 def __init__(self,client,*args):self.device=client;self.key=b'k'*32;self.expected=None
 def supported(self):return True
 async def info(self):return {'nonce':self.device.nonce,'accepted':self.device.sequence,'ota_v':1}
 async def ota_status(self):return dict(self.device.status)
 def bind_request(self,request,operation,target,nonce):self.expected=(nonce,request,operation,target)
 async def control(self,op,payload,request,service_live,should_stop):
  await service_live();d=self.device
  if self.expected[0]!=d.nonce or request!=(d.status['request'],d.status['operation'],d.status['target']):raise RuntimeError('changed')
  assert not should_stop();assert struct.unpack_from('<I',payload)[0]==request[0]
  d.frames.append((op,payload));d.sequence+=1
  if op==CATALOG_BEGIN:d.catalogue=b''
  if op==CATALOG_DATA:
   assert struct.unpack_from('<I',payload,4)[0]==len(d.catalogue);d.catalogue+=payload[8:]
  if op==CATALOG_END:d.request(0,request=0)
  return {'seq':d.sequence,'busy':False,'result':0}
def catalogue(version='0.25.1',raw=b'{"update":{"available":false}}'):
 return PreparedCatalogue(raw,b'signed',{'version':version},'0.24.0')
async def pump(relay,device,stop=lambda:False):
 async def live():device.live+=1;return False
 for _ in range(100):
  await relay.step(device,{},None,live,stop)
  if relay._handled:return
  await asyncio.sleep(.005)
 raise AssertionError(relay.notice)

def test_manual_check_bypasses_staged_and_six_hours_without_sending_an_image(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 async def forbidden(*a,**k):raise AssertionError('check must not transfer firmware')
 monkeypatch.setattr(ota_relay,'transfer_release',forbidden)
 async def run():
  calls=[];logs=[];d=Device();d.status['staged']=True
  r=ota_relay.OtaRelay(clock=lambda:10,check_catalogue=lambda v:(calls.append(v) or catalogue()),diagnostic=logs.append)
  r.last_check=10;r.staged=True
  try:
   await pump(r,d,lambda:True) # Catalogue check is allowed on battery / without download guards.
   assert calls==['0.24.0'] and d.catalogue==catalogue().raw and r.notice=='catalogue_sent'
   assert {op for op,_ in d.frames}<={7,8,9,10} and d.live==len(d.frames)
   assert any(op==CATALOG_END for op,_ in d.frames)
   for line in logs:
    record=json.loads(line.removeprefix('XQOTA '));assert set(record)=={'time','event','request','operation','bytes','elapsed_ms'}
    assert 'nonce' not in line and 'signed' not in line
  finally:r.close()
 asyncio.run(run())

def test_background_http_does_not_block_poll_or_manual_acceptance(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 gate=threading.Event();started=threading.Event()
 def background(version):started.set();gate.wait(2);return None
 async def run():
  d=Device(operation=0,request=0);ticks=[0]
  r=ota_relay.OtaRelay(clock=lambda:ticks[0],download=background,check_catalogue=lambda v:catalogue(),diagnostic=None)
  async def live():d.live+=1
  try:
   await r.step(d,{},None,live,lambda:False)
   await asyncio.to_thread(started.wait,1)
   d.request(1,request=9);ticks[0]=3
   await asyncio.wait_for(r.step(d,{},None,live,lambda:False),.2)
   assert d.frames[-1][0]==HOST_PROGRESS and struct.unpack('<IBI',d.frames[-1][1])==(9,0,0)
   ticks[0]=6;await asyncio.wait_for(r.step(d,{},None,live,lambda:False),.2)
   assert d.live==2 and r.job is not None
   gate.set();await pump(r,d);assert r.notice=='catalogue_sent'
  finally:gate.set();r.close()
 asyncio.run(run())

def test_install_only_confirmed_cached_offer_and_monotonic_progress(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 sent=[];downloads=[]
 async def send(g,key,manifest,image,service_live,stop):
  sent.append((manifest,image,g.expected));return {'phase':5}
 monkeypatch.setattr(ota_relay,'transfer_release',send)
 def download(prepared,*,target,progress,cancelled):
  downloads.append(target);progress(2);progress(1);progress(5)
  return prepared.manifest,b'image',target
 async def run():
  d=Device(2,'0.25.1',3)
  r=ota_relay.OtaRelay(check_catalogue=lambda v:(_ for _ in ()).throw(AssertionError('must use confirmed cached offer')),download_prepared=download,diagnostic=None)
  r._remember(catalogue())
  try:
   await pump(r,d)
   assert downloads==['0.25.1'] and sent[0][:2]==(b'signed',b'image')
   values=[struct.unpack('<IBI',p)[2] for op,p in d.frames if op==10 and p[4]==1]
   assert values==sorted(set(values)) and values[-1]==5 and r.staged
   # Staged does not prevent a subsequent explicit catalogue request.
   d.request(1,request=4);r.check_catalogue=lambda v:catalogue('0.25.2')
   await pump(r,d);assert r.notice=='catalogue_sent' and len(sent)==1
  finally:r.close()
 asyncio.run(run())

def test_newer_catalogue_cannot_replace_confirmed_target(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 def forbidden(*a,**k):raise AssertionError('must not download another version')
 async def run():
  d=Device(2,'0.25.1',5);r=ota_relay.OtaRelay(check_catalogue=lambda v:catalogue('0.25.2'),download_prepared=forbidden,diagnostic=None)
  try:
   await pump(r,d);assert r.notice=='download_failed'
   assert any(op==10 and struct.unpack('<IBI',p)==(5,2,2) for op,p in d.frames)
   assert not any(op in (0,1,2,3,4) for op,_ in d.frames)
  finally:r.close()
 asyncio.run(run())

def test_reconnect_discards_old_worker_result_and_restarts_new_request(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 gate=threading.Event();started=threading.Event();calls=[]
 def check(version):
  calls.append(version)
  if len(calls)==1:started.set();gate.wait(2);return catalogue(raw=b'old')
  return catalogue(raw=b'new')
 async def run():
  d=Device();r=ota_relay.OtaRelay(check_catalogue=check,diagnostic=None)
  async def live():return False
  try:
   await r.step(d,{},None,live,lambda:False);await asyncio.to_thread(started.wait,1)
   d.nonce='22'*8;d.request(1,request=2)
   await r.step(d,{},None,live,lambda:False);gate.set();await pump(r,d)
   assert d.catalogue==b'new'
   assert all(struct.unpack_from('<I',p)[0]==2 for op,p in d.frames if op in (7,8,9))
  finally:gate.set();r.close()
 asyncio.run(run())

def test_real_streaming_downloader_is_preempted_by_manual_check(monkeypatch,tmp_path):
 from ota_download import download
 from test_ota_download import release,Response,NOW
 import time
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 data=b'z'*100000;pub,envelope=release(data,version='0.25.1')
 raw=json.dumps({'update':{'available':True,'installable':True,'manifest':envelope}}).encode()
 started=threading.Event();closed=threading.Event();received=[]
 class SlowResponse(Response):
  def read(self,n):raise AssertionError('read() must not wait to fill a large buffer')
  def read1(self,n):
   assert n<=16384;started.set();time.sleep(.002);received.append(1);return b'z'
  def __exit__(self,*args):closed.set()
 class SlowOpener:
  def open(self,request,timeout):
   return Response(request.full_url,raw) if '/catalog?' in request.full_url else SlowResponse(request.full_url,data)
 def background(version,*,cancelled):return download(version,pub,SlowOpener(),NOW,tmp_path,cancelled=cancelled)
 async def run():
  d=Device(operation=0,request=0);r=ota_relay.OtaRelay(download=background,check_catalogue=lambda v:catalogue(),diagnostic=None)
  async def live():return False
  try:
   await r.step(d,{},None,live,lambda:False);assert await asyncio.to_thread(started.wait,1)
   d.request(1,request=12)
   await asyncio.wait_for(pump(r,d),.5)
   assert closed.is_set() and d.catalogue==catalogue().raw and len(received)<100
   assert not list(tmp_path.glob('*.bin'))
  finally:r.close()
 asyncio.run(run())

def test_close_reconnect_keeps_only_verified_offer_cache(monkeypatch):
 monkeypatch.setattr(ota_relay,'OTAGatt',ManualGatt)
 async def send(*a,**k):return {'phase':5}
 monkeypatch.setattr(ota_relay,'transfer_release',send)
 async def run():
  r=ota_relay.OtaRelay(check_catalogue=lambda v:catalogue('0.25.2'),download_prepared=lambda p,**kw:(p.manifest,b'image',kw['target']),diagnostic=None)
  r._remember(catalogue('0.25.1'));r.close()
  d=Device(2,'0.25.1',10);d.nonce='33'*8
  try:await pump(r,d);assert r.notice=='staged'
  finally:r.close()
 asyncio.run(run())
