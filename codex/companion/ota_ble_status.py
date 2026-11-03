"""Verify an OTA-worker status bound to the current BLE connection nonce."""
import hashlib,hmac,json,re

class OTAStatusError(ValueError):pass

def verify_status(raw,key,nonce):
 if not isinstance(raw,bytes) or len(raw)>512 or len(key)!=32 or len(nonce)!=8:
  raise OTAStatusError('invalid status input')
 def unique(pairs):
  out={}
  for k,v in pairs:
   if k in out:raise OTAStatusError('duplicate status key')
   out[k]=v
  return out
 try:
  envelope=json.loads(raw,object_pairs_hook=unique)
  if not isinstance(envelope,dict) or set(envelope)!={'data','h'}:raise OTAStatusError('invalid envelope')
  payload=envelope['data'];proof=envelope['h']
  if not isinstance(payload,str) or not isinstance(proof,str) or len(proof)!=64:raise OTAStatusError('invalid fields')
  expected=hmac.digest(key,b'XQOTA'+nonce+payload.encode('utf-8'),'sha256').hex()
  if not hmac.compare_digest(expected,proof):raise OTAStatusError('bad proof')
  data=json.loads(payload,object_pairs_hook=unique)
  base={'v','version','seq','phase','manifest','image','busy','result','staged'}
  if not isinstance(data,dict) or type(data.get('v')) is not int or data['v'] not in (1,2,3):raise OTAStatusError('invalid status version')
  if set(data)!=(base if data['v']==1 else base|{'request','operation','target'}|({'out','credit','work'} if data['v']==3 else set())):raise OTAStatusError('invalid status')
  if type(data['busy']) is not bool or type(data['staged']) is not bool:raise OTAStatusError('invalid status version')
  if not isinstance(data['version'],str) or not re.fullmatch(r'\d{1,9}\.\d{1,9}\.\d{1,9}',data['version']):raise OTAStatusError('invalid firmware version')
  for name,maximum in (('seq',0xffffffff),('phase',5),('manifest',6144),('image',0x600000)):
   if type(data[name]) is not int or not 0<=data[name]<=maximum:raise OTAStatusError('invalid counter')
  minimum=-0x80000000 if data['v']>=2 else 0
  if type(data['result']) is not int or not minimum<=data['result']<=0x7fffffff:raise OTAStatusError('invalid result')
  if data['v']>=2:
   if type(data['request']) is not int or not 0<=data['request']<=0xffffffff:raise OTAStatusError('invalid request')
   if type(data['operation']) is not int or data['operation'] not in (0,1,2):raise OTAStatusError('invalid operation')
   if (data['request']==0)!=(data['operation']==0):raise OTAStatusError('invalid request operation')
   target=data['target']
   if not isinstance(target,str) or len(target)>=32:raise OTAStatusError('invalid target')
   if data['operation']==2:
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',target):raise OTAStatusError('invalid confirmed target')
   elif target:raise OTAStatusError('unexpected target')
  if data['v']==3:
   for name,maximum in (('out',0x600000),('credit',32),('work',2)):
    if type(data[name]) is not int or not 0<=data[name]<=maximum:raise OTAStatusError('invalid stream counter')
  return data
 except (ValueError,TypeError,UnicodeError) as e:
  if isinstance(e,OTAStatusError):raise
  raise OTAStatusError('malformed status') from e


def verify_ack(raw,key,nonce):
 """Compact notification: HMAC still binds every ACK to this connection."""
 import struct
 extended=isinstance(raw,bytes) and len(raw)==66 and raw[:4]==b'XQA2'
 if not isinstance(raw,bytes) or (not extended and (len(raw)!=60 or raw[:4]!=b'XQA1')) or len(key)!=32 or len(nonce)!=8:
  raise OTAStatusError('invalid acknowledgement')
 size=34 if extended else 28
 if not hmac.compare_digest(raw[size:],hmac.digest(key,nonce+raw[:size],'sha256')):
  raise OTAStatusError('bad acknowledgement proof')
 seq,phase,busy,result,manifest,image,staged,request,op=struct.unpack('<IBBiIIBIB',raw[4:28])
 if phase>5 or busy>1 or staged>1 or manifest>6144 or image>0x600000 or op>2 or ((request==0)!=(op==0)):
  raise OTAStatusError('invalid acknowledgement values')
 extra={}
 if extended:
  out,credit,work=struct.unpack('<IBB',raw[28:34])
  if out>0x600000 or credit>32 or work>2:raise OTAStatusError('invalid stream acknowledgement')
  extra=dict(out=out,credit=credit,work=work)
 return dict(**extra,seq=seq,phase=phase,busy=bool(busy),result=result,manifest=manifest,image=image,staged=bool(staged),request=request,operation=op)


def verify_identity(raw,key,nonce):
 from ota_download import parse_json,VERSION
 if not isinstance(raw,bytes) or len(raw)>512:raise OTAStatusError('identity size')
 env=parse_json(raw)
 if not isinstance(env,dict) or set(env)!={'data','h'} or not isinstance(env['data'],str) or not isinstance(env['h'],str):raise OTAStatusError('invalid identity')
 if not hmac.compare_digest(env['h'],hmac.digest(key,b'XQBAS'+nonce+env['data'].encode(),'sha256').hex()):raise OTAStatusError('identity proof')
 d=parse_json(env['data'])
 if not isinstance(d,dict) or set(d)!=({'v','version','digest','size','delta','window'}|({'ready'} if d.get('v')==2 else set())) or type(d['v']) is not int or d['v'] not in (1,2):raise OTAStatusError('identity fields')
 if not isinstance(d['version'],str) or len(d['version'])>=32 or not VERSION.fullmatch(d['version']) or type(d['size']) is not int or not 1024<=d['size']<=0x600000 or type(d['delta']) is not bool or type(d['window']) is not int or d['window']!=32 or not isinstance(d['digest'],str) or not re.fullmatch('[0-9a-f]{64}',d['digest']):raise OTAStatusError('identity values')
 if d['v']==2 and type(d['ready']) is not bool:raise OTAStatusError('invalid boot health')
 return d
