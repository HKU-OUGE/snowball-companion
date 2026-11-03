"""BLE firmware sender; protocol wiring and device capability are pending.

Single shared GATT writer is mandatory: callers interleave other traffic only
between acknowledged packets. A failed/disconnected session is restarted from
BEGIN, never guessed or resumed from an untrusted offset.
"""
import asyncio,time,itertools,struct
from ota_ble_frames import BEGIN,MANIFEST,IMAGE_BEGIN,IMAGE,FINISH,CANCEL,BEGIN_DELTA,BEGIN_STREAM,body,chunks,packet

class OTAInterrupted(RuntimeError):pass
class OTARejected(OTAInterrupted):
    def __init__(self,result):
        super().__init__('device rejected update frame');self.result=result

async def transfer_release(transport,key,manifest,image,service_live,should_stop,*,ack_timeout=10,progress=None,delta=False):
    if not isinstance(manifest,bytes) or not isinstance(image,bytes):raise ValueError('bytes required')
    # Validate lengths and iterator creation before the first remote mutation.
    begin=body(BEGIN,total=len(manifest))
    manifests=list(chunks(manifest,MANIFEST))
    if not image or len(image)>0x600000:raise ValueError('image bounds')
    image_frames=chunks(image,IMAGE)
    initial=await transport.info()
    if initial.get('ota_v')!=1:raise OTAInterrupted('unsupported device')
    if delta and initial.get('ota_stream_v')!=2:raise OTAInterrupted('delta unsupported')
    start_op=BEGIN_DELTA if delta else BEGIN_STREAM if initial.get('ota_stream_v')==2 else BEGIN
    nonce=bytes.fromhex(initial['nonce'])
    acknowledged=None
    notifications=False
    began=time.monotonic();reported=began
    metrics=dict(packets=0,service_ms=0,info_ms=0,write_ms=0,ack_ms=0)

    def report(status,force=False):
        nonlocal reported
        now=time.monotonic()
        if progress and (force or now-reported>=10):
            progress(dict(metrics,image=status.get('image',0),total=len(image),
                transfer_ms=int((now-began)*1000),**getattr(transport,'ack_stats',{})))
            reported=now

    async def merge_live():
        nonlocal acknowledged
        tick=time.monotonic();serviced=await service_live()
        metrics['service_ms']+=int((time.monotonic()-tick)*1000)
        # FINISH is already admitted and the image window was fully drained.
        # Other messages may use later global sequences while the worker's
        # independent FINISH sequence stays constant in progress ACKs.
        if serviced is not False:acknowledged=None
        if should_stop():raise OTAInterrupted('interrupted during reconstruction')

    async def send(op,payload):
        nonlocal acknowledged
        serviced=None
        tick=time.monotonic()
        if op!=CANCEL:serviced=await service_live()  # let state and attention heartbeats run
        metrics['service_ms']+=int((time.monotonic()-tick)*1000)
        if op!=CANCEL and should_stop():raise OTAInterrupted('interrupted by activity')
        # The bridge owns the only writer. An explicit False means the live
        # service wrote nothing; the previous authenticated ACK gives the exact
        # next sequence without another slow GATT metadata round trip.
        tick=time.monotonic()
        info=({'ota_v':1,'nonce':nonce.hex(),'accepted':acknowledged}
              if serviced is False and acknowledged is not None else await transport.info())
        metrics['info_ms']+=int((time.monotonic()-tick)*1000)
        if info.get('ota_v')!=1 or bytes.fromhex(info['nonce'])!=nonce:raise OTAInterrupted('connection changed')
        seq=info['accepted']+1
        tick=time.monotonic()
        await transport.write(packet(key,nonce,seq,op,payload))
        metrics['write_ms']+=int((time.monotonic()-tick)*1000);tick=time.monotonic()
        if notifications:
            status=await transport.wait_ack(seq,60,merge=True,maintenance=merge_live) if op==FINISH and delta else await transport.wait_ack(seq,ack_timeout)
            if status['result']!=0:raise OTARejected(status['result'])
            acknowledged=seq
            metrics['packets']+=1;metrics['ack_ms']+=int((time.monotonic()-tick)*1000)
            report(status,op==FINISH)
            return status
        deadline=time.monotonic()+ack_timeout
        while True:
            status=await transport.ota_status() # caller authenticates status against nonce
            if status['seq']==seq and not status['busy']:
                if status['result']!=0:raise OTARejected(status['result'])
                acknowledged=seq
                metrics['packets']+=1;metrics['ack_ms']+=int((time.monotonic()-tick)*1000)
                report(status,op==FINISH)
                return status
            if status['seq']>seq:raise OTAInterrupted('unexpected update acknowledgement')
            if time.monotonic()>=deadline:raise OTAInterrupted('update acknowledgement timeout')
            await asyncio.sleep(.005)

    async def continuous(status,frames,window):
        nonlocal acknowledged
        iterator=iter(frames);exhausted=False;pending={};sent=acknowledged
        last_service=time.monotonic()
        async def wait(sequence,drain=False):
            nonlocal acknowledged
            tick=time.monotonic()
            reply=await transport.wait_ack(sequence,ack_timeout,cumulative=not drain)
            metrics['ack_ms']+=int((time.monotonic()-tick)*1000)
            seq=reply['seq']
            if reply['result']:raise OTARejected(reply['result'])
            if seq>sent or seq<=acknowledged or seq not in pending or reply['image']!=pending[seq]:
                raise OTAInterrupted('invalid cumulative acknowledgement')
            metrics['packets']+=sum(n<=seq for n in pending)
            for n in list(pending):
                if n<=seq:del pending[n]
            acknowledged=seq;report(reply)
            return reply
        transport.streaming=True
        try:
            while pending or not exhausted:
                if should_stop():raise OTAInterrupted('interrupted by activity')
                # A bounded slice retains one writer. Drain its admissions before
                # any session/question frame can consume the shared sequence.
                if time.monotonic()-last_service>=.25:
                    if pending:status=await wait(sent,True)
                    tick=time.monotonic();serviced=await service_live()
                    metrics['service_ms']+=int((time.monotonic()-tick)*1000)
                    if serviced is not False:
                        info=await transport.info()
                        if bytes.fromhex(info['nonce'])!=nonce:raise OTAInterrupted('connection changed')
                        acknowledged=sent=info['accepted']
                    last_service=time.monotonic()
                while not exhausted and len(pending)<window:
                    try:payload=next(iterator)
                    except StopIteration:exhausted=True;break
                    if should_stop():raise OTAInterrupted('interrupted by activity')
                    sent+=1;pending[sent]=struct.unpack_from('<I',payload)[0]+len(payload)-4
                    tick=time.monotonic();await transport.write(packet(key,nonce,sent,IMAGE,payload))
                    metrics['write_ms']+=int((time.monotonic()-tick)*1000)
                    if time.monotonic()-last_service>=.25:break
                if pending:status=await wait(min(pending),False)
            return status
        finally:transport.streaming=False

    started=False
    try:
        if hasattr(transport,'start_acknowledgements'):
            notifications=await transport.start_acknowledgements()
        await send(start_op,begin)
        started=True
        for data in manifests:await send(MANIFEST,data)
        status=await send(IMAGE_BEGIN,b'')
        window=(await transport.prepare_image_window(initial) if notifications and hasattr(transport,'prepare_image_window') else
                transport.image_window(initial) if notifications and hasattr(transport,'image_window') else 1)
        if window not in (1,4,8,32):raise OTAInterrupted('invalid stream window')
        metrics['window']=window;report(status,True)
        resumed=status.get('image',0)
        if resumed:
            if not delta or type(resumed) is not int or not 0<=resumed<=len(image):raise OTAInterrupted('invalid staged patch offset')
            image_frames=chunks(image,IMAGE,start=resumed)
        if window==32:
            status=await continuous(status,image_frames,window)
            image_frames=iter(())
        while batch:=list(itertools.islice(image_frames,window)):
            if window==1:
                await send(IMAGE,batch[0]);continue
            tick=time.monotonic();serviced=await service_live()
            metrics['service_ms']+=int((time.monotonic()-tick)*1000)
            if should_stop():raise OTAInterrupted('interrupted by activity')
            tick=time.monotonic()
            info=({'nonce':nonce.hex(),'accepted':acknowledged} if serviced is False else await transport.info())
            metrics['info_ms']+=int((time.monotonic()-tick)*1000)
            if bytes.fromhex(info['nonce'])!=nonce:raise OTAInterrupted('connection changed')
            seq=info['accepted'];transport.streaming=True
            try:
                tick=time.monotonic()
                for payload in batch:
                    if should_stop():raise OTAInterrupted('interrupted by activity')
                    seq+=1;await transport.write(packet(key,nonce,seq,IMAGE,payload))
                metrics['write_ms']+=int((time.monotonic()-tick)*1000);tick=time.monotonic()
                status=await transport.wait_ack(seq,ack_timeout)
                metrics['ack_ms']+=int((time.monotonic()-tick)*1000)
                if status['result']!=0 or status.get('image')!=struct.unpack_from('<I',batch[-1])[0]+len(batch[-1])-4:
                    raise OTAInterrupted('invalid image acknowledgement')
                acknowledged=seq;metrics['packets']+=len(batch);report(status)
            finally:transport.streaming=False
        return await send(FINISH,b'')
    except Exception:
        # The device cancels an unfinished transfer on disconnect. Do not retry
        # an ambiguous write: its completion may have happened already.
        if started:
            try:await send(CANCEL,b'')
            except Exception:pass
        raise
    finally:
        if notifications:await transport.stop_acknowledgements()
