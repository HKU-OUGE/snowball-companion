"""Portable signed delta selection. Only publishers need a patch generator.

Windows/macOS/Linux clients verify and relay bytes; all reconstruction happens
on the device's inactive slot. No shell commands or private signing keys here.
"""
import base64,hashlib,hmac,json,re,time,os,tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import HTTPError,URLError
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec,utils
from ota_download import (OTAError,MODEL,LAYOUT,KEY_ID,PUBLIC_KEY,OUTER,MAX_IMAGE,VERSION,
 parse_json,verify_manifest,PreparedCatalogue,download_prepared,_get)
from private_files import private_dir,safe_reader
FIELDS={'schema','model','layout','channel','algorithm','baseVersion','baseDigest','baseSize','version','targetSha256','targetSize','size','sha256','objectKey','issuedAt'}
class DeltaRelease(tuple):
 delta=True
 def __new__(cls,manifest,image,version):return super().__new__(cls,(manifest,image,version))

def verify_delta(envelope,identity,target,public_key=PUBLIC_KEY,now=None):
 if not isinstance(envelope,dict) or set(envelope)!=OUTER or envelope.get('keyId')!=KEY_ID:raise OTAError('delta envelope')
 raw=envelope['payload'];sig=envelope['signature']
 if not isinstance(raw,str) or len(raw.encode())>4096 or not isinstance(sig,str):raise OTAError('delta fields')
 try:
  signature=base64.b64decode(sig,validate=True)
  if len(signature)!=64:raise ValueError()
  key=ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),public_key)
  key.verify(utils.encode_dss_signature(int.from_bytes(signature[:32],'big'),int.from_bytes(signature[32:],'big')),raw.encode(),ec.ECDSA(hashes.SHA256()))
 except Exception as e:raise OTAError('delta signature') from e
 m=parse_json(raw)
 if not isinstance(m,dict) or set(m)!=FIELDS:raise OTAError('delta schema')
 if m.get('schema')!=1 or type(m['schema']) is not int or m['model']!=MODEL or m['layout']!=LAYOUT or m['channel']!='stable' or m['algorithm']!='bsdiff-heatshrink-v1':raise OTAError('delta incompatible')
 if m['baseVersion']!=identity['version'] or m['baseDigest']!=identity['digest'] or m['baseSize']!=identity['size']:raise OTAError('delta base mismatch')
 if m['version']!=target['version'] or m['targetSha256']!=target['sha256'] or m['targetSize']!=target['size']:raise OTAError('delta target mismatch')
 for field in ('baseDigest','targetSha256','sha256'):
  if not isinstance(m[field],str) or not re.fullmatch('[0-9a-f]{64}',m[field]):raise OTAError('delta digest')
 if type(m['size']) is not int or not 65<=m['size']<target['size'] or type(m['baseSize']) is not int or type(m['targetSize']) is not int:raise OTAError('delta bounds')
 if m['objectKey']!=f"delta/{MODEL}/{m['baseDigest']}/{m['version']}.xqd":raise OTAError('delta path')
 now=time.time() if now is None else now
 if type(m['issuedAt']) is not int or not 1735689600<=m['issuedAt']<=min(4102444800,now+300):raise OTAError('delta date')
 return m

def download_best(prepared,identity,*,target=None,progress=None,cancelled=None,allow_chain=False,public_key=PUBLIC_KEY,opener=None,now=None,cache_dir=None,diagnostic=None):
 if not isinstance(prepared,PreparedCatalogue):raise OTAError('invalid catalogue')
 if prepared.manifest is None:return None
 latest=verify_manifest(parse_json(prepared.manifest),public_key,prepared.current,now)
 if target is not None and latest['version']!=target:raise OTAError('target mismatch')
 if identity and identity.get('delta') is True and identity.get('version')==prepared.current:
  try:
   q={'base':identity['digest'],'baseVersion':identity['version'],'baseSize':identity['size'],'target':latest['version']}
   if allow_chain:q['chain']='1'
   for attempt in range(2):
    try:
     choice=parse_json(_get('/v1/delta?'+urlencode(q),6144,opener,cancelled=cancelled));break
    except (OSError,URLError) as error:
     if cancelled and cancelled():raise OTAError('cancelled')
     if attempt or (isinstance(error,HTTPError) and error.code<500):raise
     if diagnostic:diagnostic('delta_metadata_retry')
   if not isinstance(choice,dict) or type(choice.get('available')) is not bool:raise OTAError('invalid delta catalogue')
   if choice['available']:
    if set(choice)!={'available','target','delta'}:raise OTAError('invalid delta choice')
    selected=verify_manifest(choice['target'],public_key,prepared.current,now)
    if selected['version']!=latest['version'] and (not allow_chain or tuple(map(int,selected['version'].split('.')))>=tuple(map(int,latest['version'].split('.')))):raise OTAError('delta target changed')
    m=verify_delta(choice['delta'],identity,selected,public_key,now)
    wire=json.dumps({'target':choice['target'],'delta':choice['delta']},ensure_ascii=False,separators=(',',':')).encode()
    if len(wire)>6144:raise OTAError('delta bundle size')
    root=private_dir(cache_dir or Path.home()/'.cache/xueqiu/ota');cache=root/(m['sha256']+'.xqd');data=None
    try:
     with safe_reader(cache) as f:cached=f.read(m['size']+1)
     if len(cached)==m['size'] and hashlib.sha256(cached).hexdigest()==m['sha256']:data=cached
    except OSError:pass
    if data is None:
     data=_get('/v1/patch/'+m['objectKey'],m['size'],opener,progress=progress,cancelled=cancelled)
     if len(data)!=m['size'] or hashlib.sha256(data).hexdigest()!=m['sha256']:raise OTAError('delta hash mismatch')
     fd,tmp=tempfile.mkstemp(prefix='.patch-',dir=root)
     try:
      with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
      os.replace(tmp,cache)
     finally:
      if os.path.exists(tmp):os.unlink(tmp)
    if data[:4]!=bytes.fromhex('10decdfc') or data[4:36].hex()!=identity['digest'] or data[36:64]!=bytes(28):raise OTAError('delta header mismatch')
    if cancelled and cancelled():raise OTAError('cancelled')
    if progress:progress(len(data))
    if diagnostic:diagnostic('delta_verified')
    return DeltaRelease(wire,data,m['version'])
   if diagnostic:diagnostic('delta_not_available')
  except (OTAError,OSError,HTTPError,URLError) as error:
   # Every fallback still goes through independent full-release signature/hash
   # validation. Cancellation may never turn into another download.
   if cancelled and cancelled():raise OTAError('cancelled')
   if diagnostic:diagnostic('delta_validation_fallback' if isinstance(error,OTAError) else 'delta_network_fallback')
 return download_prepared(prepared,public_key,opener,now,cache_dir or Path.home()/'.cache/xueqiu/ota',target=target,progress=progress,cancelled=cancelled)
