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
