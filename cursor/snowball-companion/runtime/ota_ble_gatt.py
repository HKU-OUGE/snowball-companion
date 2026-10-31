"""Adapter between the BLE OTA sender and an authenticated bridge session."""
import asyncio
from ota_ble_status import verify_status

WRITE='9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
OTA_STATUS='9e7a0007-6ed4-4b6b-bf37-43dc42838c20'
class OTAGatt:
 def __init__(self,client,config,read_info,timeout=8):
  self.client,self.config,self.read_info,self.timeout=client,config,read_info,timeout
  self.key=bytes.fromhex(config['key']);self.last_nonce=None
  if len(self.key)!=32:raise ValueError('invalid device pairing key')
 def supported(self):
  return self.client.services.get_characteristic(OTA_STATUS) is not None
 async def info(self):
  info=await self.read_info(self.client,self.config)
  info=dict(info,ota_v=1 if self.supported() else 0)
  self.last_nonce=bytes.fromhex(info['nonce'])
  return info
 async def write(self,packet):
  await asyncio.wait_for(self.client.write_gatt_char(WRITE,packet,response=True),self.timeout)
 async def ota_status(self):
  if self.last_nonce is None:raise ValueError('read authenticated INFO first')
  raw=bytes(await asyncio.wait_for(self.client.read_gatt_char(OTA_STATUS),self.timeout))
  return verify_status(raw,self.key,self.last_nonce)
