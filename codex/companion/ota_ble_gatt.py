"""Adapter between the BLE OTA sender and an authenticated bridge session."""
import asyncio,time,sys
from ota_ble_status import verify_status,verify_ack,verify_identity,OTAStatusError
from ota_ble_frames import packet
from ota_ble_sender import OTAInterrupted,OTARejected
from speech_relay import VOICE,subscribe_notifications,unsubscribe_notifications

WRITE='9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
OTA_STATUS='9e7a0007-6ed4-4b6b-bf37-43dc42838c20'
class OTAGatt:
 def __init__(self,client,config,read_info,timeout=8):
  self.client,self.config,self.read_info,self.timeout=client,config,read_info,timeout
  self.key=bytes.fromhex(config['key']);self.last_nonce=None
  self.expected_request=None;self.expected_nonce=None;self._last_status=None
  self._ack_queue=None;self._notify=False;self._notify_uuid=None;self._notify_callback=None
  self._ack_fallback=False;self.ack_stats={'notifications':0,'fallback_reads':0}
  self.streaming=False
  self._control_seq=None
  self._write_capacity=None
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
  return status.get('v') in (2,3) and (status.get('request'),status.get('operation'),status.get('target'))==request
 async def write(self,packet):
  # Refresh at stream boundaries; ordinary chunks reuse the authenticated
  # preceding ACK. Native also binds/invalidate-checks every queued frame, so
  # request safety does not add a second status round trip per 192-byte chunk.
  if self.expected_request is not None and len(packet)>2 and packet[2] in (0,1,2,3,4,11,13):
   status=await self.ota_status() if packet[2] in (0,2,4,11,13) or self._last_status is None else self._last_status
   if not self.matches(status,self.expected_request):raise OTAInterrupted('request changed')
  await asyncio.wait_for(self.client.write_gatt_char(WRITE,packet,response=not(self.streaming and packet[2]==3)),self.timeout)
 def image_window(self,info):
  char=self.client.services.get_characteristic(WRITE)
  capacity=self._write_capacity if self._write_capacity is not None else getattr(char,'max_write_without_response_size',0)
  self.ack_stats.update(advertised_window=info.get('ota_window',1),write_capacity=capacity,notify_fallback=int(self._ack_fallback))
  return (32 if info.get('ota_stream_v')==2 else info['ota_window']) if (info.get('ota_window') in (4,8) and self._notify and capacity>=243) else 1
 async def prepare_image_window(self,info):
  char=self.client.services.get_characteristic(WRITE)
  self._write_capacity=getattr(char,'max_write_without_response_size',0)
  if info.get('ota_window') in (4,8) and self._write_capacity<243 and sys.platform=='linux':
   # Read BlueZ's actual negotiated MTU. A cached Bleak characteristic can
   # retain the initial 23-byte value after Service Changed. AcquireNotify
   # would steal the shared audio/OTA subscription, so never use it here.
   try:
    from dbus_fast import Message,MessageType
    backend=self.client._backend
    reply=await asyncio.wait_for(backend._bus.call(Message(destination='org.bluez',path=char.obj[0],
     interface='org.freedesktop.DBus.Properties',member='Get',signature='ss',body=['org.bluez.GattCharacteristic1','MTU'])),self.timeout)
    if reply.message_type==MessageType.METHOD_RETURN:
     mtu=reply.body[0].value
     if type(mtu) is int and 23<=mtu<=517:self._write_capacity=mtu-3
   except (AttributeError,TypeError,ValueError,asyncio.TimeoutError):pass
  if sys.platform=='win32' and self._write_capacity<243:
   for _ in range(10):
    await asyncio.sleep(.1)
    self._write_capacity=getattr(char,'max_write_without_response_size',0)
    if self._write_capacity>=243:break
  return self.image_window(info)
 async def identity(self):
  info=await self.info()
  if info.get('ota_stream_v')!=2:return None
  await self.write(packet(self.key,self.last_nonce,info['accepted']+1,12,b''))
  raw=bytes(await asyncio.wait_for(self.client.read_gatt_char(OTA_STATUS),self.timeout))
  return verify_identity(raw,self.key,self.last_nonce)
 async def ota_status(self):
  if self.last_nonce is None:raise ValueError('read authenticated INFO first')
  raw=bytes(await asyncio.wait_for(self.client.read_gatt_char(OTA_STATUS),self.timeout))
  self._last_status=verify_status(raw,self.key,self.last_nonce)
  return self._last_status
 async def start_acknowledgements(self):
  self._ack_fallback=False
  char=self.client.services.get_characteristic(OTA_STATUS)
  uuid=OTA_STATUS
  if 'notify' not in getattr(char,'properties',[]):
   # Existing bonds can cache the old read-only OTA declaration. The already
   # established audio notify channel carries separately signed XQA1 frames.
   uuid=VOICE;char=self.client.services.get_characteristic(uuid)
   if 'notify' not in getattr(char,'properties',[]):return False
  # Seed version/target with the same authenticated long-read used by old devices.
  await self.ota_status()
  queue=self._ack_queue=asyncio.Queue(maxsize=16)
  def on_ack(_sender,data):
   try:status=verify_ack(bytes(data),self.key,self.last_nonce)
   except (OTAStatusError,TypeError):return
   if queue.full():queue.get_nowait()
   queue.put_nowait(status)
  try:
   await asyncio.wait_for(subscribe_notifications(self.client,uuid,on_ack),self.timeout)
   self._notify_uuid=uuid;self._notify_callback=on_ack
   self._notify=True
  except Exception:
   self._ack_queue=None;self._notify=False
  return self._notify
 async def stop_acknowledgements(self):
  if self._notify:
   try:await asyncio.wait_for(unsubscribe_notifications(self.client,self._notify_uuid,self._notify_callback),self.timeout)
   except Exception:pass
  self._notify=False;self._ack_queue=None
 async def wait_ack(self,sequence,timeout,*,allow_request_complete=False,cumulative=False,merge=False,maintenance=None):
  deadline=time.monotonic()+timeout;hard_deadline=time.monotonic()+600;output=-1;last_maintenance=0
  while time.monotonic()<min(deadline,hard_deadline):
   if merge and maintenance is not None and time.monotonic()-last_maintenance>=.25:
    await maintenance();last_maintenance=time.monotonic()
   try:
    if self._ack_fallback:
     if self._ack_queue.empty():raise asyncio.TimeoutError
     self._ack_fallback=False
    ack=await asyncio.wait_for(self._ack_queue.get(),min(.5,max(.001,deadline-time.monotonic())))
    self.ack_stats['notifications']+=1
    if ack['result']!=0:raise OTARejected(ack['result'])
    if ack['seq']<sequence:continue
    base=self._last_status or {}
    completed=allow_request_complete and ack['seq']==sequence and not ack['request'] and not ack['operation']
    if self.expected_request is not None and (ack['request'],ack['operation'])!=self.expected_request[:2] and not completed:
     raise OTAInterrupted('request changed')
    if (ack['request'],ack['operation'])!=(base.get('request',0),base.get('operation',0)):
     # New catalogue/target requires the full authenticated descriptor.
     status=await self.ota_status()
    else:
     status={**base,**ack};self._last_status=status
   except asyncio.TimeoutError:
    # Lost notifications or an MTU below 63 bytes retain the old read fallback.
    # Do not add a half-second timeout to every 192-byte frame on a peer that
    # advertises notify but cannot deliver it. Retry notification next transfer.
    self.ack_stats['fallback_reads']+=1
    status=await self.ota_status()
    if status['seq']==sequence and not status['busy']:self._ack_fallback=True
   if status['result']!=0:raise OTARejected(status['result'])
   completed=allow_request_complete and status['seq']==sequence and not status.get('request') and not status.get('operation')
   if self.expected_request is not None and (status.get('request'),status.get('operation'))!=self.expected_request[:2] and not completed:
    raise OTAInterrupted('request changed')
   if merge and status.get('out',0)>output:
    output=status.get('out',0);deadline=time.monotonic()+timeout
   if cumulative and status['seq']>=sequence:return status
   if status['seq']>sequence:raise OTAInterrupted('unexpected update acknowledgement')
   if status['seq']==sequence and not status['busy']:return status
  raise OTAInterrupted('update acknowledgement timeout')
 async def control(self,op,payload,request,service_live,should_stop,*,ack_timeout=10):
  """Use the caller's existing writer; this method never spawns a GATT task."""
  serviced=await service_live()
  if should_stop():raise OTAInterrupted('control interrupted')
  if serviced is False and self._control_seq is not None:
   info={'accepted':self._control_seq};status=self._last_status
  else:
   info=await self.info();status=await self.ota_status()
  if not self.matches(status,request):raise OTAInterrupted('request changed')
  sequence=info['accepted']+1
  await self.write(packet(self.key,self.last_nonce,sequence,op,payload))
  if self._notify:
   status=await self.wait_ack(sequence,ack_timeout,allow_request_complete=op==9 or (op==10 and len(payload)==9 and payload[4]==2))
   self._control_seq=sequence
   return status
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
