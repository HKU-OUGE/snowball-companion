import asyncio,struct,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from ota_idle_heartbeat import IdleHeartbeat
class Services:
 def get_characteristic(self,u):return True
class Client:
 services=Services();transport='ble'
 def __init__(self):self.packets=[]
 async def write_gatt_char(self,u,p,response):self.packets.append(p)
async def info(client,config):return {'nonce':'00'*8,'accepted':len(client.packets)}
def test_idle_evidence_is_signed_and_busy_transition_is_immediate():
 async def run():
  c=Client();h=IdleHeartbeat();now=__import__('time').time();s={'version':1,'hub_alive':True,'directory_available':True,'status':'ready','generated_at':now,'pending_total':0,'activity_coverage':{'complete':True,'observed_at':now,'expires_at':now+10},'sessions':[]};config={'key':'aa'*32}
  assert await h.step(c,config,info,s);assert c.packets[-1][0]==10 and c.packets[-1][2]==6 and c.packets[-1][15]==1
  assert not await h.step(c,config,info,s)
  s['pending_total']=1;assert await h.step(c,config,info,s) and c.packets[-1][15]==0
 asyncio.run(run())
