"""Send short-lived, signed idle evidence on an already-authenticated link."""
import asyncio,time
from ota_ble_frames import IDLE_EVIDENCE,packet
from ota_ble_gatt import WRITE,OTA_STATUS
from ota_idle import idle_evidence

class IdleHeartbeat:
 def __init__(self):self.last=None;self.value=None
 async def step(self,client,config,read_info,snapshot,*,force=False):
  if getattr(client,'transport',None)=='wifi':
   if client.pipe.telemetry.get('ota_v')!=1:return False
  elif client.services.get_characteristic(OTA_STATUS) is None:return False
  now=time.monotonic();value=idle_evidence(snapshot)
  if not force and self.last is not None and now-self.last<5 and value==self.value:return False
  info=await read_info(client,config);key=bytes.fromhex(config['key']);nonce=bytes.fromhex(info['nonce'])
  p=packet(key,nonce,info['accepted']+1,IDLE_EVIDENCE,bytes([int(value)]))
  await asyncio.wait_for(client.write_gatt_char(WRITE,p,response=True),getattr(client,'io_timeout',8))
  self.last=now;self.value=value;return True
