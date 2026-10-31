"""Local preparation of a signed Snowball release for a future BLE sender.

The device independently verifies the same envelope and image. No download
here starts an installation or changes an active BLE connection.
"""
import base64, hashlib, json, re, time, os, tempfile
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils

ORIGIN='https://api.snowball.pet'
MODEL='smartring-plus-v2'
LAYOUT='snowball-ota-v1'
KEY_ID='snowball-release-2026-01'
PUBLIC_KEY=bytes.fromhex('04e5bd8fe55036d84dfad0956937eac84eb770f26553437ca4220785110d0de45da590b83fbee181bc62d096a8defeeee8d7b450fdc75a20fa2539d829180d0737')
MAX_IMAGE=0x600000
MAX_CATALOG=65536
VERSION=re.compile(r'^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$')
OUTER={'payload','signature','keyId'}
INNER={'schema','model','channel','version','layout','size','sha256','objectKey','issuedAt','notes','installable'}
class OTAError(ValueError): pass
class NoRedirect(HTTPRedirectHandler):
 def redirect_request(self,*args,**kwargs): raise OTAError('redirect rejected')
def unique_pairs(pairs):
 d={}
 for k,v in pairs:
  if k in d:raise OTAError('duplicate JSON key')
  d[k]=v
 return d
def parse_json(raw):
 try:return json.loads(raw,object_pairs_hook=unique_pairs)
 except (ValueError,TypeError,UnicodeError) as e:raise OTAError('invalid JSON') from e

def verify_manifest(envelope, public_key, current, now=None):
 if not isinstance(envelope,dict) or set(envelope)!=OUTER or envelope.get('keyId')!=KEY_ID:raise OTAError('invalid envelope')
 payload=envelope.get('payload'); sig=envelope.get('signature')
 if not isinstance(payload,str) or len(payload.encode())>4096 or not isinstance(sig,str):raise OTAError('invalid envelope fields')
 try:
  raw=base64.b64decode(sig,validate=True)
  if len(raw)!=64:raise ValueError('signature length')
  key=ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),public_key)
  key.verify(utils.encode_dss_signature(int.from_bytes(raw[:32],'big'),int.from_bytes(raw[32:],'big')),payload.encode('utf-8'),ec.ECDSA(hashes.SHA256()))
 except Exception as e:raise OTAError('signature rejected') from e
 m=parse_json(payload)
 if not isinstance(m,dict) or set(m)!=INNER:raise OTAError('invalid release fields')
 v=m.get('version');cur=VERSION.fullmatch(current) if isinstance(current,str) and len(current)<32 else None;match=VERSION.fullmatch(v) if isinstance(v,str) and len(v)<32 else None
 if not match or not cur or tuple(map(int,match.groups()))<=tuple(map(int,cur.groups())):raise OTAError('not a newer stable version')
 if m.get('model')!=MODEL or m.get('layout')!=LAYOUT or m.get('channel')!='stable' or type(m.get('schema')) is not int or m.get('schema')!=1 or m.get('installable') is not True:raise OTAError('release incompatible')
 if m.get('objectKey')!=f'firmware/{MODEL}/{v}.bin' or type(m.get('size')) is not int or not 1<=m['size']<=MAX_IMAGE:raise OTAError('image bounds')
 if not isinstance(m.get('sha256'),str) or not re.fullmatch('[0-9a-f]{64}',m['sha256']):raise OTAError('invalid digest')
 now=time.time() if now is None else now
 if type(m.get('issuedAt')) is not int or not 1735689600<=m['issuedAt']<=min(4102444800,now+300):raise OTAError('invalid release timestamp')
 if not isinstance(m.get('notes'),str) or len(m['notes'].encode())>=512 or any(ord(c)<32 and c not in '\n\t' for c in m['notes']):raise OTAError('invalid notes')
 return m

def _get(path,limit,opener=None):
 if not path.startswith('/v1/') or '\\' in path or '..' in path:raise OTAError('invalid path')
 with (opener or build_opener(ProxyHandler({}),NoRedirect())).open(Request(ORIGIN+path,headers={'Accept-Encoding':'identity','User-Agent':'SnowballCompanion/0.23.0'}),timeout=30) as r:
  if r.status!=200 or r.geturl()!=ORIGIN+path:raise OTAError('unexpected response')
  length=r.headers.get('Content-Length')
  if length is not None and (not length.isdigit() or int(length)>limit):raise OTAError('response too large')
  out=r.read(limit+1)
  if len(out)>limit:raise OTAError('response too large')
  return out

def download(current,public_key,opener=None,now=None,cache_dir=None):
 q=urlencode({'model':MODEL,'channel':'stable','current':current,'layout':LAYOUT})
 cat=parse_json(_get('/v1/catalog?'+q,MAX_CATALOG,opener));u=cat.get('update') if isinstance(cat,dict) else None
 if not isinstance(u,dict) or type(u.get('available')) is not bool:raise OTAError('invalid catalog')
 if not u['available']:return None
 if u.get('installable') is not True:raise OTAError('release not installable')
 envelope=u.get('manifest');m=verify_manifest(envelope,public_key,current,now)
 data=None;cache=None
 if cache_dir is not None:
  root=Path(cache_dir);root.mkdir(mode=0o700,parents=True,exist_ok=True);cache=root/(m['sha256']+'.bin')
  try:
   fd=os.open(cache,os.O_RDONLY|os.O_NOFOLLOW)
   with os.fdopen(fd,'rb') as stream:cached=stream.read(m['size']+1)
   if len(cached)==m['size'] and hashlib.sha256(cached).hexdigest()==m['sha256']:data=cached
  except OSError:pass
 fetched=data is None
 if fetched:
  data=_get('/v1/firmware/'+m['objectKey'],m['size'],opener)
 if len(data)!=m['size'] or hashlib.sha256(data).hexdigest()!=m['sha256']:raise OTAError('image hash mismatch')
 if cache is not None and fetched:
  fd,tmp=tempfile.mkstemp(prefix='.download-',dir=cache.parent)
  try:
   with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
   os.replace(tmp,cache)
  finally:
   if os.path.exists(tmp):os.unlink(tmp)
 wire=json.dumps(envelope,ensure_ascii=False,separators=(',',':')).encode()
 if len(wire)>6144:raise OTAError('manifest too large')
 return wire,data,m['version']

def download_official(current):
 return download(current,PUBLIC_KEY,cache_dir=Path.home()/'.cache/xueqiu/ota')
