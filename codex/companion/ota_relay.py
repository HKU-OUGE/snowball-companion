"""Cooperative BLE OTA relay. Does not activate a staged image.

This class shares the bridge's one GATT writer and services live updates between
packets. The device must advertise the authenticated OTA status characteristic.
"""
import asyncio,time
from ota_ble_gatt import OTAGatt
from ota_ble_sender import OTAInterrupted,transfer_release
from ota_download import download_official

class OtaRelay:
 def __init__(self,*,interval=21600,clock=time.monotonic,download=download_official):
  self.interval=interval;self.clock=clock;self.download=download
  self.last_check=None;self.job=None;self.ready=None;self.staged=False;self.notice=''
 async def step(self,client,config,read_info,service_live,should_stop):
  g=OTAGatt(client,config,read_info)
  if not g.supported():return
  if self.staged:return
  if self.job is not None and self.job.done():
   try:self.ready=self.job.result();self.notice='downloaded' if self.ready else 'current'
   except Exception:
    self.notice='download_failed';self.ready=None
    self.last_check=self.clock()-self.interval+60
   self.job=None
  if self.ready is not None:
   if should_stop():return
   manifest,image,version=self.ready
   try:
    result=await transfer_release(g,g.key,manifest,image,service_live,should_stop)
    if result.get('phase')!=5:raise OTAInterrupted('device did not stage image')
    self.staged=True;self.notice='staged';self.ready=None
   except Exception:
    self.notice='transfer_interrupted';self.ready=None;self.last_check=self.clock()-self.interval+60
   return
  if self.job is not None:return
  now=self.clock()
  if self.last_check is not None and now-self.last_check<self.interval:return
  info=await g.info();status=await g.ota_status()
  if status['staged']:
   self.staged=True;self.notice='staged';return
  # Only authenticated status determines the currently running version.
  version=status['version'];self.last_check=now;self.notice='checking'
  self.job=asyncio.create_task(asyncio.to_thread(self.download,version))
 def close(self):
  if self.job is not None:self.job.cancel();self.job=None
