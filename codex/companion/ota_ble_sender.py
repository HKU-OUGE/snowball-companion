"""BLE firmware sender; protocol wiring and device capability are pending.

Single shared GATT writer is mandatory: callers interleave other traffic only
between acknowledged packets. A failed/disconnected session is restarted from
BEGIN, never guessed or resumed from an untrusted offset.
"""
import asyncio,time
from ota_ble_frames import BEGIN,MANIFEST,IMAGE_BEGIN,IMAGE,FINISH,CANCEL,body,chunks,packet

class OTAInterrupted(RuntimeError):pass

async def transfer_release(transport,key,manifest,image,service_live,should_stop,*,ack_timeout=10):
    if not isinstance(manifest,bytes) or not isinstance(image,bytes):raise ValueError('bytes required')
    # Validate lengths and iterator creation before the first remote mutation.
    begin=body(BEGIN,total=len(manifest))
    manifests=list(chunks(manifest,MANIFEST))
    if not image or len(image)>0x600000:raise ValueError('image bounds')
    image_frames=chunks(image,IMAGE)
    initial=await transport.info()
    if initial.get('ota_v')!=1:raise OTAInterrupted('unsupported device')
    nonce=bytes.fromhex(initial['nonce'])
    acknowledged=None

    async def send(op,payload):
        nonlocal acknowledged
        serviced=None
        if op!=CANCEL:serviced=await service_live()  # let state and attention heartbeats run
        if op!=CANCEL and should_stop():raise OTAInterrupted('interrupted by activity')
        # The bridge owns the only writer. An explicit False means the live
        # service wrote nothing; the previous authenticated ACK gives the exact
        # next sequence without another slow GATT metadata round trip.
        info=({'ota_v':1,'nonce':nonce.hex(),'accepted':acknowledged}
              if serviced is False and acknowledged is not None else await transport.info())
        if info.get('ota_v')!=1 or bytes.fromhex(info['nonce'])!=nonce:raise OTAInterrupted('connection changed')
        seq=info['accepted']+1
        await transport.write(packet(key,nonce,seq,op,payload))
        deadline=time.monotonic()+ack_timeout
        while True:
            status=await transport.ota_status() # caller authenticates status against nonce
            if status['seq']==seq and not status['busy']:
                if status['result']!=0:raise OTAInterrupted('device rejected update frame')
                acknowledged=seq
                return status
            if status['seq']>seq:raise OTAInterrupted('unexpected update acknowledgement')
            if time.monotonic()>=deadline:raise OTAInterrupted('update acknowledgement timeout')
            await asyncio.sleep(.005)

    started=False
    try:
        await send(BEGIN,begin)
        started=True
        for data in manifests:await send(MANIFEST,data)
        await send(IMAGE_BEGIN,b'')
        for data in image_frames:await send(IMAGE,data)
        return await send(FINISH,b'')
    except Exception:
        # The device cancels an unfinished transfer on disconnect. Do not retry
        # an ambiguous write: its completion may have happened already.
        if started:
            try:await send(CANCEL,b'')
            except Exception:pass
        raise
