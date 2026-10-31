import base64,hashlib,json,sys
from pathlib import Path
import pytest
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec,utils
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'companion'))
from ota_download import *
NOW=1800000000
class Response:
 def __init__(self,url,data):self.url=url;self.data=data;self.status=200;self.headers={'Content-Length':str(len(data))}
 def geturl(self):return self.url
 def read(self,n):return self.data[:n]
 def __enter__(self):return self
 def __exit__(self,*a):pass
class Opener:
 def __init__(self,cat,image):self.cat=cat;self.image=image;self.urls=[]
 def open(self,req,timeout):
  self.urls.append(req.full_url);data=self.cat if '/v1/catalog?' in req.full_url else self.image
  return Response(req.full_url,data)
def release(data=b'image',version='1.2.3'):
 key=ec.generate_private_key(ec.SECP256R1());pub=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
 m={'schema':1,'model':MODEL,'channel':'stable','version':version,'layout':LAYOUT,'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'objectKey':f'firmware/{MODEL}/{version}.bin','issuedAt':NOW,'notes':'hello','installable':True}
 p=json.dumps(m,separators=(',',':'));r,s=utils.decode_dss_signature(key.sign(p.encode(),ec.ECDSA(hashes.SHA256())))
 e={'payload':p,'signature':base64.b64encode(r.to_bytes(32,'big')+s.to_bytes(32,'big')).decode(),'keyId':KEY_ID}
 return pub,e

def test_download_checks_signature_origin_size_and_digest():
 pub,e=release();cat=json.dumps({'update':{'available':True,'installable':True,'manifest':e}}).encode();o=Opener(cat,b'image')
 wire,img,v=download('1.2.2',pub,o,NOW);assert img==b'image' and v=='1.2.3' and json.loads(wire)==e and len(o.urls)==2
 with pytest.raises(OTAError):download('1.2.2',pub,Opener(cat,b'other'),NOW)
 with pytest.raises(OTAError):download('1.2.3',pub,Opener(cat,b'image'),NOW)
 bad=dict(e,payload=e['payload']+' ')
 with pytest.raises(OTAError):verify_manifest(bad,pub,'1.2.2',NOW)

def test_catalog_no_release_and_duplicate_json():
 pub,_=release();assert download('1.2.2',pub,Opener(b'{"update":{"available":false}}',b''),NOW) is None
 with pytest.raises(OTAError):parse_json('{"a":1,"a":2}')

def test_cache_is_revalidated_and_corruption_is_repaired(tmp_path):
 pub,e=release();cat=json.dumps({'update':{'available':True,'installable':True,'manifest':e}}).encode()
 first=Opener(cat,b'image');download('1.2.2',pub,first,NOW,cache_dir=tmp_path);assert len(first.urls)==2
 second=Opener(cat,b'wrong');assert download('1.2.2',pub,second,NOW,cache_dir=tmp_path)[1]==b'image';assert len(second.urls)==1
 cache=next(tmp_path.glob('*.bin'));cache.write_bytes(b'wrong')
 repair=Opener(cat,b'image');download('1.2.2',pub,repair,NOW,cache_dir=tmp_path)
 assert len(repair.urls)==2 and cache.read_bytes()==b'image'
