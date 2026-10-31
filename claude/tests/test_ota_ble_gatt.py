import asyncio,hmac,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'plugins/snowball-companion/runtime'))
from ota_ble_gatt import *
class Services:
 def get_characteristic(self,uuid):return object() if uuid==OTA_STATUS else None
class Client:
 services=Services()
 async def read_gatt_char(self,uuid):
  assert uuid==OTA_STATUS
  s=json.dumps({'v':1,'version':'0.22.5','seq':2,'phase':0,'manifest':0,'image':0,'busy':False,'result':0,'staged':False},separators=(',',':'))
  return json.dumps({'data':s,'h':hmac.digest(b'k'*32,b'XQOTA'+b'n'*8+s.encode(),'sha256').hex()}).encode()
 async def write_gatt_char(self,uuid,data,response):assert uuid==WRITE and data==b'frame' and response
async def info(client,config):return {'nonce':(b'n'*8).hex(),'accepted':1}
def test_authenticated_adapter():
 async def run():
  g=OTAGatt(Client(),{'key':(b'k'*32).hex()},info);assert (await g.info())['ota_v']==1
  await g.write(b'frame');assert (await g.ota_status())['version']=='0.22.5'
 asyncio.run(run())
