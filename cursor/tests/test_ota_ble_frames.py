import hashlib,hmac,struct,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from ota_ble_frames import *

def test_authenticated_frames_cover_exact_bytes_and_do_not_exceed_att_bound():
    key=bytes(range(32));nonce=bytes(range(8));data=bytes(range(256))*3
    frames=list(chunks(data,IMAGE));assert len(frames)==4
    decoded=bytearray()
    for seq,payload in enumerate(frames,1):
        assert struct.unpack_from('<I',payload)[0]==len(decoded)
        decoded.extend(payload[4:]);p=packet(key,nonce,seq,IMAGE,payload)
        assert len(p)<=272 and p[-32:]==hmac.digest(key,p[:-32],'sha256')
        assert struct.unpack_from('<BBBI8s',p)==(10,0,IMAGE,seq,nonce)
    assert decoded==data

def test_bounds_and_bad_auth_rejected():
    for call in (lambda:body(BEGIN,total=0),lambda:body(MANIFEST,6143,b'xx'),lambda:body(IMAGE,0,b'x'*193),lambda:packet(b'x',b'x'*8,1,IMAGE,b''),lambda:packet(b'x'*32,b'x'*8,0,IMAGE,b'')):
        with pytest.raises(ValueError):call()

def test_catalogue_controls_reconstruct_bounded_utf8_with_same_authenticated_header():
 raw=('目录与公告 '*1200).encode()[:16000];request=0xffffffff
 assert body(CATALOG_BEGIN,total=len(raw),request=request)==struct.pack('<II',request,len(raw))
 recovered=bytearray()
 for seq,payload in enumerate(catalogue_chunks(raw,request),1):
  rid,offset=struct.unpack_from('<II',payload);assert rid==request and offset==len(recovered)
  assert 1<=len(payload[8:])<=188;recovered+=payload[8:]
  frame=packet(b'k'*32,b'n'*8,seq,CATALOG_DATA,payload)
  assert len(frame)<=243 and frame[-32:]==hmac.digest(b'k'*32,frame[:-32],'sha256')
 assert recovered==raw
 assert body(CATALOG_END,request=request)==struct.pack('<I',request)
 assert body(HOST_PROGRESS,request=request,kind=1,value=6000)==struct.pack('<IBI',request,1,6000)
 for call in (lambda:body(CATALOG_BEGIN,total=16001,request=1),lambda:body(CATALOG_DATA,data=b'x'*189,request=1),lambda:body(CATALOG_END,request=0),lambda:body(CATALOG_END,request=True),lambda:body(HOST_PROGRESS,request=1,kind=0,value=1),lambda:body(HOST_PROGRESS,request=1,kind=2,value=0),lambda:packet(b'k'*32,b'n'*8,1,11,b'')):
  with pytest.raises(ValueError):call()
