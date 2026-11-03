import asyncio,hmac,struct,sys
import pytest
from ota_ble_status import verify_ack,OTAStatusError
from ota_ble_gatt import OTAGatt,OTA_STATUS
from ota_ble_sender import OTAInterrupted
KEY=b'k'*32;NONCE=b'n'*8
def ack(seq=1,**kw):
 d=dict(phase=3,busy=0,result=0,manifest=10,image=192,staged=0,request=5,operation=2);d.update(kw)
 raw=b'XQA1'+struct.pack('<IBBiIIBIB',seq,*[d[k] for k in ('phase','busy','result','manifest','image','staged','request','operation')])
 return raw+hmac.digest(KEY,NONCE+raw,'sha256')
def test_ack_authentication_and_bounds():
 assert verify_ack(ack(),KEY,NONCE)['image']==192
 for bad in [ack()[:-1],ack()[:-1]+bytes([ack()[-1]^1]),ack(image=0x600001),ack(phase=6),ack(request=0)]:
  with pytest.raises(OTAStatusError):verify_ack(bad,KEY,NONCE)
 with pytest.raises(OTAStatusError):verify_ack(ack(),KEY,b'x'*8)
def test_ack_fast_path_and_lost_notification_read_fallback():
 async def run():
  g=OTAGatt(None,{'key':KEY.hex()},None);g.last_nonce=NONCE;g.bind_request(5,2,'0.27.0',NONCE.hex())
  g._last_status={'v':2,'version':'0.26.3','target':'0.27.0','request':5,'operation':2};g._ack_queue=asyncio.Queue()
  reads=[]
  async def read():reads.append(1);return {**g._last_status,**verify_ack(ack(2),KEY,NONCE)}
  g.ota_status=read
  await g._ack_queue.put(verify_ack(ack(),KEY,NONCE));s=await g.wait_ack(1,1)
  assert s['seq']==1 and not reads and s['target']=='0.27.0'
  assert (await g.wait_ack(2,1))['seq']==2 and len(reads)==1
  g._ack_fallback=False
  await g._ack_queue.put(verify_ack(ack(3,request=6),KEY,NONCE))
  with pytest.raises(OTAInterrupted):await g.wait_ack(3,1)
 asyncio.run(run())


def test_notify_unavailable_does_not_add_timeout_to_every_chunk():
 async def run():
  g=OTAGatt(None,{'key':KEY.hex()},None);g.last_nonce=NONCE;g._ack_queue=asyncio.Queue()
  g._last_status={'v':2,'request':5,'operation':2,'target':'0.27.1'}
  count=0
  async def read():
   nonlocal count
   count+=1
   return {**g._last_status,**verify_ack(ack(count),KEY,NONCE)}
  g.ota_status=read
  await g.wait_ack(1,.7)
  assert g._ack_fallback and count==1
  # A fallback ACK is immediately readable even with a tiny deadline.
  assert (await g.wait_ack(2,.01))['seq']==2
  assert g.ack_stats=={'notifications':0,'fallback_reads':2}
 asyncio.run(run())


def test_shared_cached_notify_channel_preserves_audio_subscriber():
 from types import SimpleNamespace
 from speech_relay import VOICE,subscribe_notifications,unsubscribe_notifications
 class Client:
  def __init__(self):
   self.starts=[];self.stops=[];self.cb=None
   self.services=SimpleNamespace(get_characteristic=lambda uuid:SimpleNamespace(properties=['read','notify'] if uuid==VOICE else ['read']))
  async def start_notify(self,uuid,callback):self.starts.append(uuid);self.cb=callback
  async def stop_notify(self,uuid):self.stops.append(uuid)
 async def run():
  c=Client();audio=[]
  def receive(_,data):audio.append(bytes(data))
  await subscribe_notifications(c,VOICE,receive)
  g=OTAGatt(c,{'key':KEY.hex()},None);g.last_nonce=NONCE
  async def status():
   g._last_status={'v':2,'version':'0.27.1','target':'','request':0,'operation':0};return g._last_status
  g.ota_status=status
  assert await g.start_acknowledgements()
  assert c.starts==[VOICE] # No second native subscription or overwritten callback.
  c.cb(None,ack(request=0,operation=0));assert (await g.wait_ack(1,1))['seq']==1
  await g.stop_acknowledgements();assert c.stops==[]
  c.cb(None,b'XQVC example');assert audio[-1]==b'XQVC example'
  await unsubscribe_notifications(c,VOICE,receive);assert c.stops==[VOICE]
 asyncio.run(run())

@pytest.mark.skipif(sys.platform!='linux',reason='BlueZ MTU fallback; Windows uses public Bleak capacity fixture')
def test_real_mtu_refresh_keeps_cached_linux_peer_streaming(monkeypatch):
 from types import SimpleNamespace
 from dbus_fast import Message,MessageType,Variant
 import ota_ble_gatt
 async def run():
  calls=[]
  async def call(msg):
   calls.append(msg.member)
   return Message(message_type=MessageType.METHOD_RETURN,reply_serial=1,signature='v',body=[Variant('q',247)])
  char=SimpleNamespace(properties=['write'],max_write_without_response_size=20,obj=('/fixture',{}))
  client=SimpleNamespace(services=SimpleNamespace(get_characteristic=lambda _:char),_backend=SimpleNamespace(_bus=SimpleNamespace(call=call)))
  g=OTAGatt(client,{'key':KEY.hex()},None);g._notify=True
  monkeypatch.setattr(ota_ble_gatt.sys,'platform','linux')
  assert await g.prepare_image_window({'ota_window':8})==8 and calls==['Get']
  assert g.ack_stats['write_capacity']==244
  # Native Windows capacity uses the common API; never imports/calls BlueZ.
  monkeypatch.setattr(ota_ble_gatt.sys,'platform','win32');char.max_write_without_response_size=244
  assert await g.prepare_image_window({'ota_window':8})==8 and calls==['Get']
  assert await g.prepare_image_window({'ota_window':1})==1
 asyncio.run(run())
