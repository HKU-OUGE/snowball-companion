import asyncio,base64,hashlib,json,struct
from types import SimpleNamespace
import pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec,utils
import ota_delta,ota_relay,ota_ble_gatt
from ota_ble_frames import *
from ota_ble_sender import transfer_release,OTAInterrupted
from ota_download import PreparedCatalogue,OTAError,MODEL,LAYOUT,KEY_ID

@pytest.fixture
def release():
 key=ec.generate_private_key(ec.SECP256R1());pub=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
 identity=dict(version='1.0.0',size=4096,digest='ab'*32,delta=True)
 data=bytes.fromhex('10decdfc')+bytes.fromhex(identity['digest'])+bytes(28)+b'patch'*100
 target=dict(schema=1,model=MODEL,layout=LAYOUT,channel='stable',version='1.0.2',size=4096,sha256=hashlib.sha256(b'x'*4096).hexdigest(),objectKey=f'firmware/{MODEL}/1.0.2.bin',issuedAt=1800000000,notes='',installable=True)
 patch=dict(schema=1,model=MODEL,layout=LAYOUT,channel='stable',algorithm='bsdiff-heatshrink-v1',baseVersion=identity['version'],baseDigest=identity['digest'],baseSize=4096,version=target['version'],targetSha256=target['sha256'],targetSize=4096,size=len(data),sha256=hashlib.sha256(data).hexdigest(),objectKey=f"delta/{MODEL}/{identity['digest']}/1.0.2.xqd",issuedAt=1800000000)
 def sign(m):
  payload=json.dumps(m,separators=(',',':'));r,s=utils.decode_dss_signature(key.sign(payload.encode(),ec.ECDSA(hashes.SHA256())))
  return dict(payload=payload,signature=base64.b64encode(r.to_bytes(32,'big')+s.to_bytes(32,'big')).decode(),keyId=KEY_ID)
 return identity,target,patch,data,sign,pub

def test_delta_bound_to_exact_base_and_full_target(release):
 identity,target,patch,_,sign,pub=release
 assert ota_delta.verify_delta(sign(patch),identity,target,pub,1800000000)==patch
 for field,value in [('baseDigest','cd'*32),('baseSize',4000),('version','1.0.3'),('targetSha256','ef'*32),('size',5000),('issuedAt',1800000301),('objectKey','../../escape'),('algorithm','shell')]:
  with pytest.raises(OTAError):ota_delta.verify_delta(sign(dict(patch,**{field:value})),identity,target,pub,1800000000)
 bad=sign(patch);bad['payload']=bad['payload'].replace('1.0.2','1.0.3')
 with pytest.raises(OTAError):ota_delta.verify_delta(bad,identity,target,pub,1800000000)

def test_verified_patch_cache_and_safe_full_fallback(release,monkeypatch,tmp_path):
 identity,target,patch,data,sign,pub=release
 prepared=PreparedCatalogue(b'{}',json.dumps(sign(target)).encode(),target,'1.0.0');calls=[]
 def get(path,limit,*a,**kw):
  calls.append(path)
  if path.startswith('/v1/delta?'):return json.dumps(dict(available=True,target=sign(target),delta=sign(patch))).encode()
  return data
 monkeypatch.setattr(ota_delta,'_get',get)
 kw=dict(public_key=pub,now=1800000000,cache_dir=tmp_path/'中文 cache')
 out=ota_delta.download_best(prepared,identity,**kw)
 assert out.delta and out[1]==data and out[2]=='1.0.2'
 out=ota_delta.download_best(prepared,identity,**kw)
 assert sum('/v1/patch/' in p for p in calls)==1
 monkeypatch.setattr(ota_delta,'download_prepared',lambda *a,**kw:(b'full',b'image','1.0.2'))
 wrong=dict(identity,digest='cd'*32)
 assert ota_delta.download_best(prepared,wrong,**kw)[0]==b'full'
 with pytest.raises(OTAError):ota_delta.download_best(prepared,identity,cancelled=lambda:True,**kw)

def test_auto_chain_accepts_intermediate_but_manual_pins_target(release,monkeypatch,tmp_path):
 identity,target,patch,data,sign,pub=release
 prepared=PreparedCatalogue(b'{}',json.dumps(sign(target)).encode(),target,'1.0.0')
 mid=dict(target,version='1.0.1',objectKey=f'firmware/{MODEL}/1.0.1.bin');patch=dict(patch,version='1.0.1',objectKey=f"delta/{MODEL}/{identity['digest']}/1.0.1.xqd")
 monkeypatch.setattr(ota_delta,'_get',lambda path,*a,**kw:json.dumps(dict(available=True,target=sign(mid),delta=sign(patch))).encode() if path.startswith('/v1/delta?') else data)
 monkeypatch.setattr(ota_delta,'download_prepared',lambda *a,**kw:(b'full',b'image','1.0.2'))
 kw=dict(public_key=pub,now=1800000000,cache_dir=tmp_path)
 assert ota_delta.download_best(prepared,identity,allow_chain=True,**kw)[2]=='1.0.1'
 assert ota_delta.download_best(prepared,identity,target='1.0.2',**kw)[2]=='1.0.2'

class StreamPeer:
 def __init__(self):self.seq=0;self.done=0;self.queue=[];self.data=bytearray();self.streaming=False;self.max_pending=0;self.ops=[];self.resumed=False
 async def info(self):return dict(ota_v=1,ota_stream_v=2,nonce='6e'*8,accepted=self.seq,ota_window=8)
 async def start_acknowledgements(self):return True
 async def stop_acknowledgements(self):pass
 def image_window(self,info):return 32
 async def write(self,p):
  seq=struct.unpack_from('<I',p,3)[0];assert seq==self.seq+1;self.seq=seq;self.ops.append(p[2])
  if p[2]==IMAGE:self.queue.append((seq,p[15:-32]));self.max_pending=max(self.max_pending,len(self.queue));assert len(self.queue)<=32
  else:self.done=seq
  await asyncio.sleep(.001)
 async def wait_ack(self,seq,timeout,*,cumulative=False,merge=False,maintenance=None):
  if cumulative:
   batch,self.queue=self.queue[:4],self.queue[4:]
  else:batch,self.queue=self.queue,[]
  for n,p in batch:
   assert struct.unpack_from('<I',p)[0]==len(self.data);self.data.extend(p[4:]);self.done=n
  return dict(seq=self.done,phase=5 if self.ops[-1]==FINISH else 3,result=0,busy=bool(self.queue),image=len(self.data))

def test_continuous_sender_cumulative_ack_and_live_writer_order():
 async def run():
  p=StreamPeer();services=[]
  async def service():
   assert not p.queue
   services.append(len(p.data));p.seq+=1;p.done=p.seq;return True
  data=bytes(range(251))*1000
  out=await transfer_release(p,b'k'*32,b'm',data,service,lambda:False)
  assert bytes(p.data)==data and out['phase']==5 and p.max_pending==32 and BEGIN_STREAM in p.ops
  assert any(0<n<len(data) for n in services)
 asyncio.run(run())

def test_windows_uses_bleak_capacity_without_unix_backend(monkeypatch):
 char=SimpleNamespace(max_write_without_response_size=244,properties=['notify'])
 client=SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _:char))
 g=ota_ble_gatt.OTAGatt(client,{'key':'11'*32},None);g._notify=True
 monkeypatch.setattr(ota_ble_gatt,'sys',SimpleNamespace(platform='win32'))
 assert asyncio.run(g.prepare_image_window({'ota_window':8,'ota_stream_v':2}))==32
 char.max_write_without_response_size=20
 assert asyncio.run(g.prepare_image_window({'ota_window':8,'ota_stream_v':2}))==1

def test_new_boot_rechecks_instead_of_waiting_six_hours(monkeypatch):
 class G:
  def __init__(self,*a):pass
  def supported(self):return True
  async def info(self):return dict(nonce='11'*8)
  async def ota_status(self):return dict(version='1.0.1',staged=False)
 monkeypatch.setattr(ota_relay,'OTAGatt',G)
 async def run():
  called=[];r=ota_relay.OtaRelay(clock=lambda:100,download=lambda v:called.append(v),diagnostic=None)
  r._current='1.0.0';r.staged=True;r.last_check=99
  await r.step(None,{},None,None,lambda:False)
  await asyncio.sleep(.05)
  assert called==['1.0.1'] and not r.staged
  r.close()
 asyncio.run(run())

def test_resume_sends_only_durable_patch_suffix():
 class Resume(StreamPeer):
  async def wait_ack(self,seq,timeout,**kw):
   s=await super().wait_ack(seq,timeout,**kw)
   if self.ops[-1]==IMAGE_BEGIN:
    self.data=bytearray(b'x'*65536);s['image']=65536
   return s
 async def run():
  peer=Resume()
  async def live():return False
  image=b'x'*70000
  await transfer_release(peer,b'k'*32,b'm',image,live,lambda:False,delta=True)
  assert bytes(peer.data)==image and peer.ops.count(IMAGE)==24 and BEGIN_DELTA in peer.ops
 asyncio.run(run())

def test_audio_collector_ignores_both_ota_ack_versions():
 from speech_relay import SpeechRelay
 r=SpeechRelay(None);r.collector=object()
 # A synthetic collector deliberately has no audio methods: neither OTA
 # frame may enter the speech decoder or change capture state.
 r.notify(None,b'XQA1'+bytes(56));r.notify(None,b'XQA2'+bytes(62))
 assert r.collector is not None

@pytest.mark.parametrize('ready,expected',[(True,True),(False,False),(None,False)])
def test_background_waits_for_device_boot_health(ready,expected,monkeypatch):
 class G:
  def __init__(self,*a):pass
  def supported(self):return True
  async def info(self):return dict(nonce='11'*8,ota_stream_v=2)
  async def ota_status(self):return dict(version='1.0.1',staged=False)
  async def identity(self):return dict(version='1.0.1',**({'ready':ready} if ready is not None else {}))
 monkeypatch.setattr(ota_relay,'OTAGatt',G)
 async def run():
  calls=[];clock=[100];r=ota_relay.OtaRelay(clock=lambda:clock[0],download=lambda v:calls.append(v),diagnostic=None)
  await r.step(None,{},None,None,lambda:False);await asyncio.sleep(.03)
  assert bool(calls)==expected
  if ready is None:
   clock[0]=136;await r.step(None,{},None,None,lambda:False);await asyncio.sleep(.03);assert calls==['1.0.1']
  r.close()
 asyncio.run(run())

def test_identity_health_is_signed_and_strict():
 import hmac
 from ota_ble_status import verify_identity,OTAStatusError
 key=b'k'*32;nonce=b'n'*8
 d=dict(v=2,version='1.0.0',digest='ab'*32,size=4096,delta=True,window=32,ready=False)
 def wire(d):
  payload=json.dumps(d,separators=(',',':'));return json.dumps(dict(data=payload,h=hmac.digest(key,b'XQBAS'+nonce+payload.encode(),'sha256').hex())).encode()
 assert verify_identity(wire(d),key,nonce)['ready'] is False
 with pytest.raises(OTAStatusError):verify_identity(wire(dict(d,ready=1)),key,nonce)
 with pytest.raises(OTAStatusError):verify_identity(wire(d).replace(b'false',b'true'),key,nonce)

def test_reconstruction_progress_keeps_live_service_and_extends_only_on_progress():
 async def run():
  g=ota_ble_gatt.OTAGatt(None,{'key':'11'*32},None);g._ack_queue=asyncio.Queue();g._last_status=dict(v=3,request=0,operation=0,target='')
  services=[]
  async def live():services.append(True)
  async def produce():
   for i in range(10):
    await g._ack_queue.put(dict(seq=100,phase=4 if i<9 else 5,busy=i<9,result=0,manifest=100,image=1000,staged=i==9,request=0,operation=0,out=i*1000,credit=31,work=1 if i<9 else 0))
    await asyncio.sleep(.05)
  task=asyncio.create_task(produce())
  try:
   result=await g.wait_ack(100,.15,merge=True,maintenance=live)
   assert result['staged'] and len(services)>=2
  finally:await task
 asyncio.run(run())
