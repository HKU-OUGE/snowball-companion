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
  if not isinstance(data,dict) or type(data.get('v')) is not int or data['v'] not in (1,2):raise OTAStatusError('invalid status version')
  if set(data)!=(base if data['v']==1 else base|{'request','operation','target'}):raise OTAStatusError('invalid status')
  if type(data['busy']) is not bool or type(data['staged']) is not bool:raise OTAStatusError('invalid status version')
  if not isinstance(data['version'],str) or not re.fullmatch(r'\d{1,9}\.\d{1,9}\.\d{1,9}',data['version']):raise OTAStatusError('invalid firmware version')
  for name,maximum in (('seq',0xffffffff),('phase',5),('manifest',6144),('image',0x600000)):
   if type(data[name]) is not int or not 0<=data[name]<=maximum:raise OTAStatusError('invalid counter')
  minimum=-0x80000000 if data['v']==2 else 0
  if type(data['result']) is not int or not minimum<=data['result']<=0x7fffffff:raise OTAStatusError('invalid result')
  if data['v']==2:
   if type(data['request']) is not int or not 0<=data['request']<=0xffffffff:raise OTAStatusError('invalid request')
   if type(data['operation']) is not int or data['operation'] not in (0,1,2):raise OTAStatusError('invalid operation')
   if (data['request']==0)!=(data['operation']==0):raise OTAStatusError('invalid request operation')
   target=data['target']
   if not isinstance(target,str) or len(target)>=32:raise OTAStatusError('invalid target')
   if data['operation']==2:
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)',target):raise OTAStatusError('invalid confirmed target')
   elif target:raise OTAStatusError('unexpected target')
  return data
 except (ValueError,TypeError,UnicodeError) as e:
  if isinstance(e,OTAStatusError):raise
  raise OTAStatusError('malformed status') from e
