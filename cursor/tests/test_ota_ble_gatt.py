import asyncio,hmac,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from ota_ble_gatt import *
class Services:
 def get_characteristic(self,uuid):return object() if uuid==OTA_STATUS else None
class Client:
 services=Services()
 async def read_gatt_char(self,uuid):
  assert uuid==OTA_STATUS
  s=json.dumps({'v':1,'version':'0.22.5','seq':2,'phase':0,'manifest':0,'image':0,'busy':False,'result':0,'staged':False},separators=(',',':'))
  return json.dumps({'data':s,'h':hmac.digest(b'k'*32,b'XQOTA'+b'n'*8+s.encode(),'sha256').hex()}).encode()
 async def write_gatt_char(self,uuid,data,response):assert uuid==WRITE and data==b'frame' and response
async def info(client,config):return {'nonce':(b'n'*8).hex(),'accepted':1}
def test_authenticated_adapter():
 async def run():
  g=OTAGatt(Client(),{'key':(b'k'*32).hex()},info);assert (await g.info())['ota_v']==1
  await g.write(b'frame');assert (await g.ota_status())['version']=='0.22.5'
 asyncio.run(run())

import struct,pytest
from ota_ble_frames import body,packet,HOST_PROGRESS,CATALOG_END,IMAGE_BEGIN
from ota_ble_sender import OTAInterrupted
class ManualClient:
 services=Services()
 def __init__(self):
  self.nonce=b'n'*8;self.accepted=0;self.writes=[]
  self.status={'v':2,'version':'0.24.0','seq':0,'phase':0,'manifest':0,'image':0,'busy':False,'result':0,'staged':False,'request':5,'operation':1,'target':''}
 async def read_gatt_char(self,uuid):
  s=json.dumps(self.status,separators=(',',':'))
  return json.dumps({'data':s,'h':hmac.digest(b'k'*32,b'XQOTA'+self.nonce+s.encode(),'sha256').hex()},separators=(',',':')).encode()
 async def write_gatt_char(self,uuid,data,response):
  version,provider,op,seq,nonce=struct.unpack_from('<BBBI8s',data)
  assert version==10 and provider==0 and seq==self.accepted+1 and nonce==self.nonce
  assert data[-32:]==hmac.digest(b'k'*32,data[:-32],'sha256')
  self.writes.append(data);self.accepted=seq;self.status['seq']=seq
  if op==CATALOG_END:self.status.update(request=0,operation=0,target='')
async def manual_info(client,config):return {'nonce':client.nonce.hex(),'accepted':client.accepted}

def test_controls_share_current_sequence_and_accept_final_request_clear():
 async def run():
  c=ManualClient();g=OTAGatt(c,{'key':(b'k'*32).hex()},manual_info)
  g.bind_request(5,1,'',c.nonce.hex());live=[]
  async def service():live.append(1);c.accepted+=1;return True
  result=await g.control(HOST_PROGRESS,body(HOST_PROGRESS,request=5),(5,1,''),service,lambda:False)
  assert result['seq']==2 and len(c.writes)==1
  result=await g.control(CATALOG_END,body(CATALOG_END,request=5),(5,1,''),service,lambda:False)
  assert result['request']==0 and result['seq']==4 and len(live)==2
 asyncio.run(run())

def test_request_replacement_and_reconnect_cannot_send_old_controls_or_images():
 async def run():
  c=ManualClient();g=OTAGatt(c,{'key':(b'k'*32).hex()},manual_info);g.bind_request(5,1,'',c.nonce.hex())
  async def service():return False
  c.status['request']=6
  with pytest.raises(OTAInterrupted):await g.control(HOST_PROGRESS,body(HOST_PROGRESS,request=5),(5,1,''),service,lambda:False)
  assert not c.writes
  c.status.update(request=5,operation=2,target='0.25.1');g.bind_request(5,2,'0.25.1',c.nonce.hex());await g.info()
  c.status['target']='0.25.2'
  with pytest.raises(OTAInterrupted):await g.write(packet(g.key,c.nonce,1,IMAGE_BEGIN,b''))
  assert not c.writes
  c.nonce=b'x'*8
  with pytest.raises(OTAInterrupted):await g.info()
 asyncio.run(run())

def test_ordinary_image_chunks_reuse_verified_ack_without_double_read():
 from ota_ble_frames import IMAGE
 async def run():
  c=ManualClient();c.status.update(operation=2,target='0.25.1');reads=[]
  original=c.read_gatt_char
  async def read(uuid):reads.append(uuid);return await original(uuid)
  c.read_gatt_char=read
  g=OTAGatt(c,{'key':(b'k'*32).hex()},manual_info);g.bind_request(5,2,'0.25.1',c.nonce.hex())
  await g.info();await g.ota_status();baseline=len(reads)
  for seq in range(1,6):
   await g.write(packet(g.key,c.nonce,seq,IMAGE,body(IMAGE,offset=(seq-1)*192,data=b'x'*192)))
   await g.ota_status()
  assert len(reads)-baseline==5 # One ACK read per image packet, not two.
  c.status.update(request=6,operation=1,target='');await g.ota_status()
  with pytest.raises(OTAInterrupted):await g.write(packet(g.key,c.nonce,6,IMAGE,body(IMAGE,offset=960,data=b'x')))
  assert len(c.writes)==5
 asyncio.run(run())
