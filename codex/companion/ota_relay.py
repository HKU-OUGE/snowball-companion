"""Request-bound BLE OTA. HTTP workers never access the shared GATT writer."""
import asyncio,json,time,threading,inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
from functools import partial
from ota_ble_gatt import OTAGatt
from ota_ble_sender import OTAInterrupted,OTARejected,transfer_release
from ota_ble_frames import CATALOG_BEGIN,CATALOG_DATA,CATALOG_END,HOST_PROGRESS,MAX_IMAGE,body,catalogue_chunks
from ota_delta import download_best
from ota_download import download_official,check_catalogue_official,download_prepared_official,PreparedCatalogue,OTAError

class OtaRelay:
 def __init__(self,*,interval=21600,clock=time.monotonic,download=download_official,
              check_catalogue=check_catalogue_official,download_prepared=download_prepared_official,
              diagnostic=print,wall_clock=time.time):
  self.interval=interval;self.clock=clock;self.download=download
  self.check_catalogue=check_catalogue;self.download_prepared=download_prepared
  self.diagnostic=diagnostic;self.wall_clock=wall_clock
  self.last_check=None;self.job=None;self.ready=None;self.staged=False;self.notice=''
  self._executor=None;self._cancel=threading.Event();self._lock=threading.Lock()
  self._request=None;self._job_kind=None;self._handled=False
  self._accepted=False;self._progress=0;self._sent_progress=0;self._heartbeat=None
  self._catalogue_ready=None;self._catalogues={};self._started=0
  self._identity=None;self._current=None;self._force_full=False;self._delta_rejected=set();self._version_seen_at=None
 def _note(self,event):
  if event==self.notice:return
  self.notice=event;token=self._request
  record={'time':datetime.fromtimestamp(self.wall_clock(),timezone.utc).isoformat(timespec='seconds'),
          'event':event,'request':token[1] if token else 0,'operation':token[2] if token else 0,
          'bytes':self._progress,'elapsed_ms':max(0,int((self.clock()-self._started)*1000)) if self._started else 0}
  # Never log exception strings, nonce, keys, URLs, device or task identifiers.
  if self.diagnostic:
   try:self.diagnostic('XQOTA '+json.dumps(record,separators=(',',':')))
   except Exception:pass
 def _cancel_job(self):
  self._cancel.set()
  if self.job is not None:self.job.cancel()
  self.job=None;self._job_kind=None
 def _start(self,kind,function,cancel=None):
  if self._executor is None:self._executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='snowball-ota')
  self._cancel=cancel or threading.Event();self._job_kind=kind
  self.job=asyncio.get_running_loop().run_in_executor(self._executor,function)
 def _remember(self,catalogue):
  if isinstance(catalogue,PreparedCatalogue) and catalogue.metadata:
   self._catalogues[catalogue.metadata['version']]=catalogue
   while len(self._catalogues)>4:del self._catalogues[next(iter(self._catalogues))]
 def _worker_kwargs(self,function,cancel):
  # Old injected download(version) callables remain supported. Production
  # HTTP functions accept cooperative cancellation and bounded read1 chunks.
  try:parameters=inspect.signature(function).parameters
  except (ValueError,TypeError):return {}
  accepts='cancelled' in parameters or any(p.kind==inspect.Parameter.VAR_KEYWORD for p in parameters.values())
  return {'cancelled':cancel.is_set} if accepts else {}
 def _start_check(self,current):
  cancel=threading.Event()
  self._start('check',partial(self.check_catalogue,current,**self._worker_kwargs(self.check_catalogue,cancel)),cancel)
 def _start_auto(self,current):
  cancel=threading.Event()
  if self.download is download_official:
   def prepare():
    catalogue=self.check_catalogue(current,cancelled=cancel.is_set)
    identity=None if catalogue.metadata and (current,catalogue.metadata['version']) in self._delta_rejected else self._identity
    return download_best(catalogue,identity,allow_chain=True,cancelled=cancel.is_set,diagnostic=self._note)
   self._start('auto',prepare,cancel)
  else:self._start('auto',partial(self.download,current,**self._worker_kwargs(self.download,cancel)),cancel)
 def _start_install(self,current,target):
  cached=self._catalogues.get(target);cancelled=threading.Event()
  def progress(value):
   if type(value) is int and 0<=value<=MAX_IMAGE:
    with self._lock:
     if not cancelled.is_set():self._progress=max(self._progress,value)
  def prepare():
   catalogue=cached or self.check_catalogue(current,**self._worker_kwargs(self.check_catalogue,cancelled))
   if cancelled.is_set():raise OTAError('cancelled')
   if not isinstance(catalogue,PreparedCatalogue) or not catalogue.metadata or catalogue.metadata['version']!=target:
    raise OTAError('confirmed target unavailable')
   release=(download_best(catalogue,None if (current,target) in self._delta_rejected else self._identity,target=target,progress=progress,cancelled=cancelled.is_set,diagnostic=self._note)
            if self.download_prepared is download_prepared_official and not self._force_full else
            self.download_prepared(catalogue,target=target,progress=progress,cancelled=cancelled.is_set))
   if cancelled.is_set():raise OTAError('cancelled')
   if not release or release[2]!=target:raise OTAError('confirmed target mismatch')
   return catalogue,release
  self._start('install',prepare,cancelled)
 async def _control(self,g,op,payload,service_live):
  return await g.control(op,payload,self._request[1:],service_live,lambda:False)
 async def _progress_control(self,g,service_live,*,force=False):
  now=self.clock()
  with self._lock:progress=self._progress
  if progress>self._sent_progress:
   await self._control(g,HOST_PROGRESS,body(HOST_PROGRESS,request=self._request[1],kind=1,value=progress),service_live)
   self._sent_progress=progress;self._heartbeat=now
  elif force or self._heartbeat is None or now-self._heartbeat>=2:
   await self._control(g,HOST_PROGRESS,body(HOST_PROGRESS,request=self._request[1],kind=0,value=0),service_live)
   self._heartbeat=now
 async def _fail(self,g,service_live,error,notice):
  self._cancel_job();self.ready=None;self._catalogue_ready=None;self._handled=True;self._note(notice)
  if self._request:
   try:await self._control(g,HOST_PROGRESS,body(HOST_PROGRESS,request=self._request[1],kind=2,value=error),service_live)
   except Exception:pass
 def _transfer_progress(self,metrics):
  # Numeric transport timings only; never exception text or user content.
  allowed={'packets','service_ms','info_ms','write_ms','ack_ms','image','total','transfer_ms','notifications','fallback_reads','window','advertised_window','write_capacity','notify_fallback'}
  record={'event':'transfer_progress',**{k:v for k,v in metrics.items() if k in allowed and type(v) is int and v>=0}}
  if self.diagnostic:
   try:self.diagnostic('XQOTA '+json.dumps(record,separators=(',',':')))
   except Exception:pass
 async def step(self,client,config,read_info,service_live,should_stop):
  g=OTAGatt(client,config,read_info)
  if not g.supported():return
  # Manual requests are polled even while staged/downloading or rate limited.
  info=await g.info();status=await g.ota_status()
  current=status['version']
  if current!=self._current:self._version_seen_at=self.clock()
  if self._current is not None and current!=self._current:
   self._cancel_job();self.ready=None;self.staged=False;self.last_check=None;self._request=None;self._identity=None
   self._note('version_changed_recheck')
  self._current=current
  token=(info['nonce'],status['request'],status['operation'],status['target']) if status.get('v') in (2,3) and status['operation'] else None
  if token!=self._request:
   self._cancel_job();self.ready=None;self._catalogue_ready=None
   self._request=token;self._handled=False;self._accepted=False;self._force_full=False
   with self._lock:self._progress=0
   self._sent_progress=0;self._heartbeat=None;self._started=self.clock()
   if token:
    self.last_check=self.clock();self._note('manual_check' if token[2]==1 else 'manual_install')
  self.staged=bool(status['staged'])
  if token:
   g.bind_request(token[1],token[2],token[3],token[0])
   if self._handled:return
   if not self._accepted:
    await self._progress_control(g,service_live,force=True);self._accepted=True
   if token[2]==2 and should_stop():
    await self._fail(g,service_live,4,'manual_paused');return
   # A matching staged image is activated by native code after owner release.
   if token[2]==2 and status['staged']:
    await self._progress_control(g,service_live);self._note('staged_waiting');return
  elif self.staged:
   self._note('staged');return
  if self.job is not None and self.job.done():
   kind=self._job_kind;job=self.job;self.job=None;self._job_kind=None
   try:
    result=job.result()
    if kind=='check':
     self._remember(result);self._catalogue_ready=result
    elif kind=='install':
     catalogue,self.ready=result;self._remember(catalogue);self._note('downloaded')
    else:
     self.ready=result;self._note('downloaded' if result else 'current')
   except (Exception,asyncio.CancelledError):
    if token:
     await self._fail(g,service_live,1 if token[2]==1 else 2,'catalogue_failed' if token[2]==1 else 'download_failed')
    else:
     self._note('download_failed');self.ready=None;self.last_check=self.clock()-self.interval+60
    return
  if token and self._catalogue_ready is not None:
   catalogue=self._catalogue_ready;self._catalogue_ready=None
   notify=False
   try:
    if not isinstance(catalogue,PreparedCatalogue):raise OTAError('invalid catalogue')
    if hasattr(g,'start_acknowledgements'):notify=await g.start_acknowledgements()
    await self._control(g,CATALOG_BEGIN,body(CATALOG_BEGIN,total=len(catalogue.raw),request=token[1]),service_live)
    for payload in catalogue_chunks(catalogue.raw,token[1]):
     await self._control(g,CATALOG_DATA,payload,service_live)
    await self._control(g,CATALOG_END,body(CATALOG_END,request=token[1]),service_live)
    self._handled=True;self._note('catalogue_sent')
   except Exception:
    await self._fail(g,service_live,3,'catalogue_interrupted')
   finally:
    if notify:await g.stop_acknowledgements()
   return
  if self.ready is not None:
   if should_stop():
    if token:await self._fail(g,service_live,4,'manual_paused')
    return
   manifest,image,version=self.ready
   if token and version!=token[3]:
    await self._fail(g,service_live,3,'target_mismatch');return
   try:
    if token:await self._progress_control(g,service_live)
    elif status.get('v') in (2,3):g.bind_request(0,0,'',info['nonce'])
    kwargs={'progress':self._transfer_progress} if 'progress' in inspect.signature(transfer_release).parameters else {}
    if getattr(self.ready,'delta',False):kwargs['delta']=True;self._note('delta_transfer')
    result=await transfer_release(g,g.key,manifest,image,service_live,should_stop,**kwargs)
    if result.get('phase')!=5:raise OTAInterrupted('device did not stage image')
    self.staged=True;self._handled=bool(token);self.ready=None;self._note('staged')
   except Exception as error:
    if isinstance(error,OTARejected):self._note('device_rejected_'+str(error.result))
    if getattr(self.ready,'delta',False) and isinstance(error,OTARejected) and error.result not in (0x103,0x107) and not should_stop():
     self._delta_rejected.add((status['version'],version))
     if len(self._delta_rejected)>8:self._delta_rejected={ (status['version'],version) }
     # One full-image retry, never an unbounded delta/whole-image loop. A
     # reconnect changes the request token and is checked before every frame.
     self.ready=None;self._identity=None;self._force_full=True;self._note('delta_full_fallback')
     if token:self._start_install(status['version'],token[3])
     else:
      cancel=threading.Event();self._start('auto',partial(self.download,status['version'],**self._worker_kwargs(self.download,cancel)),cancel)
     return
    if token:await self._fail(g,service_live,2,'transfer_interrupted')
    else:
     self._note('transfer_interrupted');self.ready=None;self.last_check=self.clock()-self.interval+60
   return
  if self.job is not None:
   if token:await self._progress_control(g,service_live)
   return
  if token:
   if token[2]==1:self._start_check(status['version'])
   else:
    self._identity=await g.identity() if info.get('ota_stream_v')==2 and hasattr(g,'identity') else None
    self._start_install(status['version'],token[3])
   return
  now=self.clock()
  if self.last_check is not None and now-self.last_check<self.interval:return
  self.last_check=now;self._started=now;self._note('checking')
  self._identity=await g.identity() if info.get('ota_stream_v')==2 and hasattr(g,'identity') else None
  if self._identity and (self._identity.get('ready') is False or ('ready' not in self._identity and self._version_seen_at is not None and now-self._version_seen_at<35)):
   self.last_check=now-self.interval+5;self._note('waiting_boot_health');return
  self._start_auto(status['version'])
 def close(self):
  self._cancel_job();self.ready=None;self._catalogue_ready=None;self._request=None
  if self._executor is not None:self._executor.shutdown(wait=False,cancel_futures=True);self._executor=None
