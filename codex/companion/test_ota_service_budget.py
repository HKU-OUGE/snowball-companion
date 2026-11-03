"""Regression: slow live I/O must leave airtime for firmware data."""
import ast,asyncio
from pathlib import Path
from types import SimpleNamespace

def test_slow_live_service_does_not_repeat_before_each_image_packet():
 tree=ast.parse(Path(__file__).with_name('bridge.py').read_text())
 fn=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='ota_service_live')
 wrapper=ast.parse('def make():\n last_ota_service=0.0\n last_ota_bulk=0.0\n return None\n').body[0]
 wrapper.body[-1:]=[fn,ast.Return(value=ast.Name(id='ota_service_live',ctx=ast.Load()))]
 module=ast.fix_missing_locations(ast.Module(body=[wrapper],type_ignores=[]))
 clock=[100.0];calls=[];client=SimpleNamespace(_snowball_write_count=0)
 class Slow:
  def __init__(self,name):self.name=name
  async def step(self,*args,**kwargs):
   calls.append(self.name);clock[0]+=.3
   if self.name=='attention':client._snowball_write_count+=1
 g={'time':SimpleNamespace(monotonic=lambda:clock[0]),'args':SimpleNamespace(source=None),
    'parse_source':lambda v:None,'client':client,'config':{},'read_info':None,
    **{k:Slow(k) for k in ('attention','relay','idle_heartbeat','power','speech','catalogue','summaries')}}
 exec(compile(module,'live_service','exec'),g);service=g['make']()
 async def run():
  assert await service() is True
  assert len(calls)==8
  for _ in range(4):
   clock[0]+=.05
   assert await service() is False
  clock[0]+=.6
  assert await service() is True
  assert len(calls)==14
 asyncio.run(run())

def test_write_observer_is_idempotent_and_does_not_count_failed_writes():
 from bridge import observe_client_writes
 async def run():
  async def send(*args,**kwargs):
   if args[0]=='fail':raise RuntimeError('fixture')
  client=SimpleNamespace(write_gatt_char=send)
  observe_client_writes(client);observe_client_writes(client)
  await client.write_gatt_char('write',b'data',response=True)
  await client.write_gatt_char('write',b'data',response=False)
  assert client._snowball_write_count==2
  try:await client.write_gatt_char('fail',b'')
  except RuntimeError:pass
  else:raise AssertionError('write error swallowed')
  assert client._snowball_write_count==2
 asyncio.run(run())
