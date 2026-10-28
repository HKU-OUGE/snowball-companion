import asyncio,importlib.util,json,sys,hmac
from pathlib import Path
root=Path(__file__).resolve().with_name('bridge.py');sp=importlib.util.spec_from_file_location('bridge',root);b=importlib.util.module_from_spec(sp);sp.loader.exec_module(b)
from bleak import BleakClient
from bleak.exc import BleakError
async def main():
 config=json.loads(b.CONFIG.read_text());device=await b.find_device(config);agent=await b.linux_agent(device);results={}
 try:
  async with BleakClient(device,timeout=20) as client:
   count=0
   for provider in b.PROVIDERS:
    for state in b.STATES:
     await b.send(client,config,provider,state);count+=1
   results['state_provider_combinations_applied']=count;print('Applied combinations:',count,flush=True)
   before=await b.read_info(client,config);key=bytes.fromhex(config['key']);valid=b.make_packet(key,before,'Codex','running');await client.write_gatt_char(b.WRITE,valid,response=True);await asyncio.sleep(.15);ack=await b.read_info(client,config);assert ack['applied']==before['accepted']+1
   packets={'replayed_sequence':valid,'short_packet':b'bad'}
   bad=bytearray(b.make_packet(key,ack,'Codex','completed'));bad[-1]^=1;packets['wrong_hmac']=bytes(bad)
   bad=bytearray(b.make_packet(key,ack,'Codex','completed'));bad[7]^=1;bad[15:]=hmac.digest(key,bytes(bad[:15]),'sha256');packets['wrong_boot_nonce']=bytes(bad)
   bad=bytearray(b.make_packet(key,ack,'Codex','completed'));bad[2]=6;bad[15:]=hmac.digest(key,bytes(bad[:15]),'sha256');packets['invalid_state']=bytes(bad)
   for name,packet in packets.items():
    try:await asyncio.wait_for(client.write_gatt_char(b.WRITE,packet,response=True),8)
    except BleakError:results[name]='rejected'
    else:raise AssertionError(name+' unexpectedly accepted')
    after=await b.read_info(client,config);assert after['accepted']==ack['accepted'] and after['applied']==ack['applied'];results[name+'_no_ui_mutation']=True;print(name,results[name],flush=True)
   # Hold the authenticated transport idle to cross the 15-second stale threshold.
   await asyncio.sleep(16)
   after=await b.send(client,config,'Codex','idle');results['recovers_after_heartbeat_gap']=after['state']==0
 finally:
  if agent:agent.disconnect()
 Path('ble-hardware-test-result.json').write_text(json.dumps(results,indent=2)+'\n');print(json.dumps(results,indent=2))
if __name__ == "__main__":
 asyncio.run(main())
