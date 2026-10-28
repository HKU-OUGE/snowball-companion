"""Bounded authenticated microphone upload; only explicit device confirmation sends."""
import asyncio,base64,hashlib,hmac,http.client,json,os,struct,subprocess,time,zlib
from pathlib import Path
VOICE='9e7a0005-6ed4-4b6b-bf37-43dc42838c20'
WRITE='9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
HUB_TARGET_ERRORS=frozenset({'question_no_longer_pending','answer_already_submitted',
    'answer_unavailable','message_unavailable'})

def diagnostic(event,**fields):
    print(json.dumps({'time':time.time(),'speech':event,**fields},separators=(',',':')),flush=True)

class SpeechServiceError(ValueError):
    def __init__(self,code,status):
        super().__init__(code);self.code=code;self.status=status

def local_http(path,payload=None,port=4191,timeout=65):
    token=(Path.home()/'.config/xueqiu/hub-token').read_text().strip()
    c=http.client.HTTPConnection('127.0.0.1',port,timeout=timeout)
    try:
        body=json.dumps(payload) if payload is not None else None
        c.request('POST' if body is not None else 'GET',path,body,{'Authorization':'Bearer '+token,'Content-Type':'application/json'})
        r=c.getresponse()
        # Hub answer acknowledgements include the current session snapshot.
        # Keep the same bounded 1 MiB budget as the existing attention adapter.
        limit=1024*1024 if port==4190 else 65536
        data=r.read(limit+1)
        if len(data)>limit:raise ValueError('speech_service_unavailable')
        response=json.loads(data)
        if r.status!=200:
            reason=response.get('error')
            if port==4190:
                if reason not in HUB_TARGET_ERRORS:reason='delivery_unconfirmed'
            elif reason not in {'invalid_audio','audio_too_large','unsupported_audio','no_speech','speech_busy','speech_too_long','speech_unavailable'}:reason='speech_service_unavailable'
            raise SpeechServiceError(reason,r.status)
        return response
    finally:c.close()

def ensure_service():
    """Optional configuration; no downloads or environment changes at bridge startup."""
    config=Path.home()/'.config/xueqiu/speech.json'
    if not config.is_file():return False
    try:
        if local_http('/v1/health',timeout=.5).get('ready'):return True
    except (OSError,ValueError,http.client.HTTPException):pass
    try:
        settings=json.loads(config.read_text());python=Path(settings['python']);model=Path(settings['model'])
        if not python.is_file() or not (model/'model.bin').is_file():return False
        folder=Path.home()/'.local/state/xueqiu/speech';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        with (folder/'service.log').open('ab') as log:
            os.chmod(folder/'service.log',0o600)
            subprocess.Popen([str(python),str(Path(__file__).with_name('speech_service.py')),'--config',str(config)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        return False # Health polling will report ready after the model loads.
    except (OSError,ValueError,KeyError):return False

def parse_meta(raw,key,nonce):
    if len(raw)>512:raise ValueError('invalid_speech_metadata')
    envelope=json.loads(raw);data=envelope['data']
    body=json.dumps(data,separators=(',',':')).encode()
    digest=hmac.digest(key,b'XQVOICE'+nonce+body,'sha256').hex()
    if not hmac.compare_digest(envelope.get('h',''),digest):raise ValueError('invalid_speech_proof')
    if set(data)!={'v','id','s','p','sid','qid','n','samples','crc','busy'} or data['v'] not in (1,2,3):raise ValueError('invalid_speech_metadata')
    for field in ['v','id','s','p','n','samples','crc','busy']:
        if type(data[field]) is not int:raise ValueError('invalid_speech_metadata')
    if not (0<=data['id']<=0xffffffff and 0<=data['s']<=8 and data['p']==0 and 0<=data['n']<=160000 and 0<=data['samples']<=320000 and 0<=data['crc']<=0xffffffff and data['busy'] in (0,1)):raise ValueError('invalid_speech_metadata')
    import re
    for name in ['sid','qid']:
        if not isinstance(data[name],str) or (data[name] and not re.fullmatch('[a-f0-9]{32}',data[name])):raise ValueError('invalid_speech_target')
    if data['s'] in (3,4) and (not data['sid'] or data['n']*2!=data['samples'] or data['samples']<8000):raise ValueError('invalid_speech_audio')
    return data

class Collector:
    def __init__(self,meta,key,nonce):
        self.meta=meta;self.key=key;self.nonce=nonce;self.data=bytearray(meta['n']);self.present=bytearray(meta['n']);self.at=0;self.chunk_size=160
    def receive(self,packet):
        if not 29<=len(packet)<=188 or packet[:4]!=b'XQVC':raise ValueError('invalid_speech_chunk')
        body,proof=packet[:-16],packet[-16:]
        if not hmac.compare_digest(proof,hmac.digest(self.key,self.nonce+body,'sha256')[:16]):raise ValueError('invalid_speech_proof')
        identity,offset=struct.unpack('<II',body[4:12]);payload=body[12:]
        if identity!=self.meta['id']:return
        if offset+len(payload)>len(self.data):raise ValueError('invalid_speech_offset')
        for i,value in enumerate(payload,offset):
            if self.present[i] and self.data[i]!=value:raise ValueError('conflicting_speech_chunk')
            self.data[i]=value;self.present[i]=1
        self.chunk_size=max(self.chunk_size if self.at else 0,len(payload))
        while self.at<len(self.data) and self.present[self.at]:self.at+=1
    def complete(self):
        return self.at==len(self.data) and zlib.crc32(self.data)==self.meta['crc']

def bound_target(payload,meta,now=None):
    now=time.time() if now is None else now
    if not isinstance(payload,dict) or not payload.get('hub_alive') or not now-5<=payload.get('generated_at',0)<=now+5:raise ValueError('stale_target')
    if meta['sid']=='0'*32 and not meta['qid']:
        if payload.get('can_create') is True:return 'new-independent-session'
        raise ValueError('session_unavailable')
    for row in payload.get('sessions',[]):
        identity=row['source']+'/'+row['session_id']
        if row['provider']=='Codex' and row['source']=='codex-hooks' and hashlib.sha256(identity.encode()).hexdigest()[:32]==meta['sid']:
            if meta['qid'] and not any(q.get('id')==meta['qid'] and q.get('answerable') for q in row.get('questions',[])):raise ValueError('question_changed')
            return identity
    raise ValueError('session_unavailable')

class SpeechRelay:
    def __init__(self,read_info):
        self.read_info=read_info;self.attached=False;self.unsupported=False;self.ready=False
        self.last_meta=0;self.last_health=0;self.last_lease=0;self.last_poll=0;self.health_task=None;self.nonce=None
        self.collector=None;self.job=None;self.task=None;self.window_end=0;self.pull_at=0;self.receive_error=False
    async def close(self):
        # Consume pending worker failures on disconnect. A confirmed send may
        # still complete in its thread; never retry it after losing the receipt.
        tasks=[t for t in (self.health_task,self.task) if t is not None]
        for task in tasks:
            if not task.done():task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        self.health_task=self.task=None;self.collector=None;self.job=None
    @property
    def transferring(self):return self.collector is not None
    @property
    def latency_sensitive(self):
        return self.collector is not None or bool(self.task and self.job and self.job['phase'] in ('transcribing','sending'))
    async def write(self,client,config,op,payload):
        info=await self.read_info(client,config)
        if self.nonce!=info['nonce']:raise ValueError('speech_device_restarted')
        head=struct.pack('<BBBI8s',8,0,op,info['accepted']+1,bytes.fromhex(self.nonce))+payload
        await asyncio.wait_for(client.write_gatt_char(WRITE,head+hmac.digest(bytes.fromhex(config['key']),head,'sha256'),response=True),8)
    def notify(self,_,data):
        if self.collector:
            try:
                packet=bytes(data)
                if packet[:1]==b'{':
                    # BlueZ also emits Value PropertiesChanged after ReadValue
                    # on this read+notify characteristic. Bleak forwards it to
                    # the same callback. Verify metadata before ignoring the
                    # echo; it is not an audio packet or an upload failure.
                    parse_meta(packet,self.collector.key,self.collector.nonce)
                    return
                self.collector.receive(packet)
            except (ValueError,KeyError,TypeError) as error:
                if not self.receive_error:
                    diagnostic('packet_rejected',reason=str(error),bytes=len(data),
                        kind='audio' if bytes(data[:4])==b'XQVC' else 'json' if bytes(data[:1])==b'{' else 'other')
                self.receive_error=True
    async def status(self,client,config,identity,status):
        await self.write(client,config,4,struct.pack('<IB',identity,status))
    async def step(self,client,config,payload):
        if self.unsupported:return
        now=time.monotonic()
        interval=.04 if self.collector else .15 if self.task else .5 if self.job else 2.0
        if self.attached and now-self.last_poll<interval:return
        self.last_poll=now
        if not self.attached:
            if not client.services.get_characteristic(VOICE):self.unsupported=True;return
            await client.start_notify(VOICE,self.notify);self.attached=True
            await asyncio.to_thread(ensure_service)
        if self.health_task and self.health_task.done():
            try:self.ready=bool(self.health_task.result().get('ready'))
            except (OSError,ValueError,http.client.HTTPException):self.ready=False
            self.health_task=None
        if now-self.last_health>5 and not self.health_task:
            self.last_health=now;self.health_task=asyncio.create_task(asyncio.to_thread(local_http,'/v1/health',None,4191,1))
        # Every control write checks fresh signed INFO. There is no need for an
        # additional long INFO read on each packet-window polling iteration.
        if self.nonce is None:
            info=await self.read_info(client,config)
            self.nonce=info['nonce'];self.last_lease=0
        if now-self.last_lease>5:
            await self.write(client,config,0,bytes([self.ready]));self.last_lease=now
        if now-self.last_meta>(2 if self.collector or self.task else .5):
            self.last_meta=now
            meta=parse_meta(bytes(await client.read_gatt_char(VOICE)),bytes.fromhex(config['key']),bytes.fromhex(self.nonce))
            if self.job and meta['id']!=self.job['id']:
                # Late results never retarget a new recording or another chat.
                self.collector=None;self.job=None;self.task=None
            if meta['s']==0:
                self.collector=None;self.job=None;self.task=None;return
            if meta['s'] in (3,4) and not meta['busy'] and not self.job:
                try:target=bound_target(payload,meta)
                except ValueError as error:
                    diagnostic('target_rejected',reason=str(error));await self.status(client,config,meta['id'],9);return
                if not self.ready:await self.status(client,config,meta['id'],4);return
                self.job={'id':meta['id'],'target':target,'question':meta['qid'],'phase':'upload','started':now,'progress_at':now,'received':0}
                diagnostic('upload_started',id=meta['id'],bytes=meta['n'],seconds=round(meta['samples']/16000,3))
                self.collector=Collector(meta,bytes.fromhex(config['key']),bytes.fromhex(self.nonce));self.receive_error=False;self.window_end=0;self.pull_at=0
            if meta['s'] in (5,6) and not self.job:
                # A restarted bridge has no reviewed transcript or send receipt.
                # Never leave an orphaned confirmation spinning or send twice.
                diagnostic('orphaned_confirmation',id=meta['id'],sending=meta['s']==6)
                await self.status(client,config,meta['id'],5 if meta['s']==6 else 9)
                return
            if meta['s']==6 and self.job and self.job['phase']=='review':
                request={'session':self.job['target'],'text':self.job['text'],
                    'request_id':hashlib.sha256((self.nonce+':voice:'+str(meta['id'])).encode()).hexdigest()[:32]}
                endpoint='/v1/message'
                if self.job['target']=='new-independent-session':
                    request.pop('session');endpoint='/v1/session/create'
                if self.job['question']:request['question']=self.job['question'];endpoint='/v1/answer'
                self.job['phase']='sending'
                self.task=asyncio.create_task(asyncio.to_thread(local_http,endpoint,request,4190,45))
        if self.collector:
            c=self.collector
            if c.at>self.job['received']:
                self.job['received']=c.at;self.job['progress_at']=now
            if self.receive_error or now-self.job['progress_at']>10 or now-self.job['started']>90:
                diagnostic('upload_failed',id=self.job['id'],received=c.at,total=len(c.data),reason='invalid_packet' if self.receive_error else 'timeout')
                await self.status(client,config,self.job['id'],8);self.collector=None;self.job['phase']='failed';return
            if c.at==len(c.data):
                if not c.complete():
                    diagnostic('upload_failed',id=self.job['id'],reason='crc')
                    await self.status(client,config,self.job['id'],8);self.collector=None;self.job['phase']='failed';return
                diagnostic('upload_verified',id=self.job['id'],bytes=len(c.data),elapsed=round(now-self.job['started'],3))
                if c.meta['v']>=2:
                    await self.write(client,config,5,struct.pack('<I',self.job['id']))
                request={'encoding':'ima-adpcm','audio':base64.b64encode(c.data).decode(),'samples':c.meta['samples']}
                self.task=asyncio.create_task(asyncio.to_thread(local_http,'/v1/transcribe',request))
                self.job['phase']='transcribing';self.collector=None
            elif c.at>=self.window_end or now-self.pull_at>(1.5 if c.meta['v']>=3 else .5):
                offset=c.at
                count=96 if c.meta['v']>=3 else 24
                await self.write(client,config,1,struct.pack('<IIB',self.job['id'],offset,count));self.pull_at=now
                # Notifications may arrive while the write acknowledgement is
                # awaited. Bound the window from the requested offset, not the
                # now-advanced receive cursor.
                self.window_end=min(len(c.data),offset+count*c.chunk_size)
        if self.task and self.task.done() and self.job:
            task=self.task;self.task=None
            try:response=task.result()
            except (OSError,ValueError,http.client.HTTPException) as error:
                diagnostic('job_failed',id=self.job['id'],phase=self.job['phase'],
                    reason=error.code if isinstance(error,SpeechServiceError) else type(error).__name__,
                    http_status=getattr(error,'status',None))
                code=5 if self.job['phase']=='sending' else {'no_speech':6,'speech_busy':7}.get(getattr(error,'code',''),10)
                if (self.job['phase']=='sending' and isinstance(error,SpeechServiceError)
                        and error.status==409 and error.code in HUB_TARGET_ERRORS):
                    # The Hub rejected this exact target before delivery.
                    # Never turn a stale answer into a fresh instruction.
                    code=9
                await self.status(client,config,self.job['id'],code);self.job['phase']='failed';return
            if self.job['phase']=='transcribing':
                text=response.get('text','');raw=text.encode()
                if not text.strip() or len(text)>256 or len(raw)>768:await self.status(client,config,self.job['id'],3);self.job['phase']='failed';return
                await self.write(client,config,2,struct.pack('<IH',self.job['id'],len(raw)))
                for offset in range(0,len(raw),180):await self.write(client,config,3,struct.pack('<IH',self.job['id'],offset)+raw[offset:offset+180])
                await self.status(client,config,self.job['id'],1);self.job.update(text=text,phase='review')
                diagnostic('review_ready',id=self.job['id'],characters=len(text))
            elif self.job['phase']=='sending':
                await self.status(client,config,self.job['id'],2 if response.get('delivery')=='accepted' else 5)
                self.job['phase']='sent';self.job.pop('text',None)
                diagnostic('delivery_result',id=self.job['id'],accepted=response.get('delivery')=='accepted')
