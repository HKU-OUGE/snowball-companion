"""Authenticated BLE OTA frame construction; receiver integration is pending.

Never send until firmware advertises this exact protocol. The manifest is
verified on the device; this module deliberately does not select boot slots.
"""
import hmac
import struct

PACKET_VERSION=10
MAX_CHUNK=192
MAX_MANIFEST=6144
MAX_IMAGE=0x600000
BEGIN,MANIFEST,IMAGE_BEGIN,IMAGE,FINISH,CANCEL,IDLE_EVIDENCE=range(7)


def body(op, offset=0, data=b'', total=0):
    if type(data) is not bytes or type(offset) is not int or type(total) is not int:
        raise ValueError('invalid OTA frame types')
    if op == BEGIN:
        if not 1 <= total <= MAX_MANIFEST or data or offset: raise ValueError('manifest size')
        return struct.pack('<I',total)
    if op in (MANIFEST,IMAGE):
        limit=MAX_MANIFEST if op==MANIFEST else MAX_IMAGE
        if not 1 <= len(data) <= MAX_CHUNK or not 0 <= offset < limit or len(data)>limit-offset or total:
            raise ValueError('chunk bounds')
        return struct.pack('<I',offset)+data
    if op in (IMAGE_BEGIN,FINISH,CANCEL):
        if data or offset or total: raise ValueError('unexpected payload')
        return b''
    raise ValueError('unknown operation')


def packet(key,nonce,sequence,op,payload):
    if not isinstance(key,bytes) or len(key)!=32 or not isinstance(nonce,bytes) or len(nonce)!=8:
        raise ValueError('invalid authentication material')
    if type(sequence) is not int or not 1<=sequence<=0xffffffff:
        raise ValueError('invalid sequence')
    if op not in range(7) or not isinstance(payload,bytes) or len(payload)>MAX_CHUNK+4:
        raise ValueError('invalid OTA payload')
    header=struct.pack('<BBBI8s',PACKET_VERSION,0,op,sequence,nonce)+payload
    return header+hmac.digest(key,header,'sha256')


def chunks(data,op):
    if not isinstance(data,bytes) or not data or op not in (MANIFEST,IMAGE): raise ValueError('invalid data')
    limit=MAX_MANIFEST if op==MANIFEST else MAX_IMAGE
    if len(data)>limit: raise ValueError('data too large')
    for offset in range(0,len(data),MAX_CHUNK):
        yield body(op,offset,data[offset:offset+MAX_CHUNK])
