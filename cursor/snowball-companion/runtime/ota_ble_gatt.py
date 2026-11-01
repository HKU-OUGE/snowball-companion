"""Adapter between the BLE OTA sender and an authenticated bridge session."""
import asyncio,time
from ota_ble_status import verify_status
from ota_ble_frames import packet
from ota_ble_sender import OTAInterrupted

WRITE='9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
OTA_STATUS='9e7a0007-6ed4-4b6b-bf37-43dc42838c20'
class OTAGatt:
 def __init__(self,client,config,read_info,timeout=8):
  self.client,self.config,self.read_info,self.timeout=client,config,read_info,timeout
  self.key=bytes.fromhex(config['key']);self.last_nonce=None
  self.expected_request=None;self.expected_nonce=None;self._last_status=None
  if len(self.key)!=32:raise ValueError('invalid device pairing key')
 def supported(self):
  return self.client.services.get_characteristic(OTA_STATUS) is not None
 async def info(self):
  info=await self.read_info(self.client,self.config)
  info=dict(info,ota_v=1 if self.supported() else 0)
  nonce=bytes.fromhex(info['nonce'])
  if nonce!=self.last_nonce:self._last_status=None
  self.last_nonce=nonce
  if self.expected_nonce is not None and self.last_nonce!=self.expected_nonce:raise OTAInterrupted('connection changed')
  return info
 def bind_request(self,request,operation,target,nonce):
  self.expected_request=(request,operation,target);self.expected_nonce=bytes.fromhex(nonce)
 def matches(self,status,request):
  return status.get('v')==2 and (status.get('request'),status.get('operation'),status.get('target'))==request
 async def write(self,packet):
  # Refresh at stream boundaries; ordinary chunks reuse the authenticated
  # preceding ACK. Native also binds/invalidate-checks every queued frame, so
  # request safety does not add a second status round trip per 192-byte chunk.
  if self.expected_request is not None and len(packet)>2 and packet[2]<=4:
   status=await self.ota_status() if packet[2] in (0,2,4) or self._last_status is None else self._last_status
   if not self.matches(status,self.expected_request):raise OTAInterrupted('request changed')
  await asyncio.wait_for(self.client.write_gatt_char(WRITE,packet,response=True),self.timeout)
 async def ota_status(self):
  if self.last_nonce is None:raise ValueError('read authenticated INFO first')
  raw=bytes(await asyncio.wait_for(self.client.read_gatt_char(OTA_STATUS),self.timeout))
  self._last_status=verify_status(raw,self.key,self.last_nonce)
  return self._last_status
 async def control(self,op,payload,request,service_live,should_stop,*,ack_timeout=10):
  """Use the caller's existing writer; this method never spawns a GATT task."""
  await service_live()
  if should_stop():raise OTAInterrupted('control interrupted')
  info=await self.info();status=await self.ota_status()
  if not self.matches(status,request):raise OTAInterrupted('request changed')
  sequence=info['accepted']+1
  await self.write(packet(self.key,self.last_nonce,sequence,op,payload))
  deadline=time.monotonic()+ack_timeout
  while True:
   status=await self.ota_status()
   if status['seq']==sequence and not status['busy']:
    if status['result']!=0:raise OTAInterrupted('control rejected')
    # CATALOG_END/error can legitimately complete and clear the request.
    return status
   if status['seq']>sequence:raise OTAInterrupted('unexpected control acknowledgement')
   if time.monotonic()>=deadline:raise OTAInterrupted('control acknowledgement timeout')
   await asyncio.sleep(.005)
