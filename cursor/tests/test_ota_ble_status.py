import hmac,json,sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from ota_ble_status import verify_status,OTAStatusError

def test_status_requires_current_connection_and_unmodified_result():
 key=b'k'*32;nonce=b'n'*8
 data=json.dumps({'v':1,'version':'0.22.5','seq':7,'phase':3,'manifest':4,'image':192,'busy':False,'result':0,'staged':False},separators=(',',':'))
 mac=hmac.digest(key,b'XQOTA'+nonce+data.encode(),'sha256').hex();raw=json.dumps({'data':data,'h':mac}).encode()
 assert verify_status(raw,key,nonce)['seq']==7
 for sample,n in ((raw,b'x'*8),(raw.replace(b'192',b'193'),nonce),(b'{"data":"x","data":"y","h":"x"}',nonce)):
  with pytest.raises(OTAStatusError):verify_status(sample,key,n)

def test_v2_requests_are_strict_and_v1_is_not_silently_upgraded():
 key=b'k'*32;nonce=b'n'*8
 base={'v':2,'version':'0.24.0','seq':7,'phase':0,'manifest':0,'image':0,'busy':False,'result':0,'staged':False,'request':42,'operation':2,'target':'0.25.1'}
 def signed(value):
  payload=json.dumps(value,separators=(',',':'));return json.dumps({'data':payload,'h':hmac.digest(key,b'XQOTA'+nonce+payload.encode(),'sha256').hex()},separators=(',',':')).encode()
 assert verify_status(signed(base),key,nonce)==base
 for result in (-1,-0x80000000,0x7fffffff):
  assert verify_status(signed(dict(base,result=result)),key,nonce)['result']==result
 for result in (-0x80000001,0x80000000,True):
  with pytest.raises(OTAStatusError):verify_status(signed(dict(base,result=result)),key,nonce)
 for patch in ({'request':True},{'operation':True},{'operation':3},{'request':0},{'target':''},{'target':'0.25.01'},{'target':'x'*32},{'operation':1},{'v':1},{'unexpected':0}):
  with pytest.raises(OTAStatusError):verify_status(signed(dict(base,**patch)),key,nonce)
 for patch in ({'request':0,'operation':0,'target':''},{'operation':1,'target':''}):
  assert verify_status(signed(dict(base,**patch)),key,nonce)['operation']==patch['operation']
 missing=dict(base);del missing['target']
 with pytest.raises(OTAStatusError):verify_status(signed(missing),key,nonce)
