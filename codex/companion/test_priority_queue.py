"""Priority changes use isolated state; never modifies live tasks."""
import tempfile, unittest, json, struct, hmac, time
from pathlib import Path
from unittest.mock import patch, AsyncMock, Mock
from status_hub import StatusHub, Invalid
import bridge

class QueueTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.now=1700000000.;self.hub=StatusHub(Path(self.tmp.name),lambda:self.now);self.seq={}
 def tearDown(self):self.tmp.cleanup()
 def event(self,id,phase='running',provider='Codex',**kw):
  seq=self.seq[id]=self.seq.get(id,0)+1
  self.hub.accept(dict(version=1,source='test',session_id=id,provider=provider,event_id=f'{id}-{seq}',seq=seq,timestamp=self.now,kind='task',phase=phase,ttl_seconds=300,**kw))
 def focus(self):return self.hub.snapshot().get('session_id')
 def move(self,id,before=None):return self.hub.set_focus({'provider':'Codex','move':'test/'+id,'before':'test/'+before if before else None})
 def test_completion_idle_yield_resume_original_priority(self):
  self.event('a');self.now+=1;self.event('b');self.assertEqual(self.focus(),'a')
  self.event('a','completed');self.assertEqual(self.focus(),'b')
  self.event('a','running');self.assertEqual(self.focus(),'a')
  self.event('a','idle');self.assertEqual(self.focus(),'b')
  self.event('a','thinking');self.assertEqual(self.focus(),'a')
 def test_alerts_preempt_and_then_restore_queue(self):
  for id in ['a','b','c']:self.event(id)
  self.event('c','waiting',pending_input=1);self.assertEqual(self.focus(),'c')
  self.event('b','failed');self.assertEqual(self.focus(),'b')
  self.event('b','running');self.assertEqual(self.focus(),'c')
  self.event('c','running',pending_input=0);self.assertEqual(self.focus(),'a')
 def test_move_persist_reconnect_and_new_append(self):
  for id in ['a','b','c']:self.event(id)
  self.hub.set_focus({'source':'test','session_id':'a'})
  result=self.move('c','a');self.assertFalse(result['focus_pinned']);self.assertEqual(self.focus(),'c')
  self.event('d');self.assertEqual(self.focus(),'c')
  self.hub=StatusHub(Path(self.tmp.name),lambda:self.now);self.assertEqual(self.focus(),'c')
  self.move('c');self.assertEqual(self.focus(),'a')
  self.assertEqual(self.hub.snapshot()['priority_order'],['test/a','test/b','test/d','test/c'])
 def test_late_events_and_heartbeat_never_promote(self):
  self.event('a');self.event('b');self.now+=10;self.event('b','thinking');self.assertEqual(self.focus(),'a')
  self.hub.accept(dict(version=1,source='test',session_id='b',provider='Codex',event_id='hb',seq=3,timestamp=self.now,kind='heartbeat',ttl_seconds=300));self.assertEqual(self.focus(),'a')
 def test_expired_error_does_not_preempt_current_work(self):
  self.event('a','failed');self.now+=301;self.event('b');self.assertEqual(self.focus(),'b')
 def test_pending_survives_activity_expiry(self):
  self.event('a','waiting',pending_input=1);self.now+=301;self.event('b');self.assertEqual(self.focus(),'a')
 def test_invalid_move_atomic_and_provider_scope(self):
  self.event('a');self.event('b',provider='Claude');old=list(self.hub.priority_order)
  for data in [{'provider':'Codex','move':'test/a','before':'test/b'},{'provider':'Codex','move':'unknown','before':None}]:
   with self.assertRaises(Invalid):self.hub.set_focus(data)
   self.assertEqual(self.hub.priority_order,old)
 def test_migration_preserves_registration_order(self):
  self.event('a');self.event('b');path=self.hub.store_path;d=json.loads(path.read_text());d.pop('priority_order');path.write_text(json.dumps(d));self.hub=StatusHub(Path(self.tmp.name),lambda:self.now);self.assertEqual(self.focus(),'a')
 def test_catalogue_includes_fresh_terminal_and_keeps_order(self):
  self.event('a','completed');self.event('b');self.event('c','waiting')
  rows,total,*_=bridge.CatalogRelay().catalogue(self.hub.snapshot(),'Codex',self.now)
  self.assertEqual([r['session_id'] for r in rows],['a','b','c']);self.assertEqual(total,2)
  self.move('c','a');rows,*_=bridge.CatalogRelay().catalogue(self.hub.snapshot(),'Codex',self.now);self.assertEqual([r['session_id'] for r in rows],['c','a','b'])

 def test_batch_applies_once_and_preserves_unlisted_slots(self):
  self.event('a');self.event('foreign',provider='Claude');self.event('b');self.event('c')
  self.hub.set_focus({'source':'test','session_id':'a'})
  with patch.object(self.hub,'persist',wraps=self.hub.persist) as save:
   result=self.hub.set_focus({'provider':'Codex','order':['test/c','test/b','test/a']})
   self.assertEqual(save.call_count,1)
  self.assertEqual(result['priority_order'],['test/c','test/foreign','test/b','test/a'])
  self.assertFalse(result['focus_pinned']);self.assertEqual(self.focus(),'c')
  restarted=StatusHub(Path(self.tmp.name),lambda:self.now);self.assertEqual(restarted.priority_order,result['priority_order'])
 def test_bad_batch_never_partially_changes_order_or_focus(self):
  self.event('a');self.event('b');self.event('foreign',provider='Claude')
  self.hub.set_focus({'source':'test','session_id':'a'});before=self.hub.store_path.read_bytes()
  for order in [[],['test/a','test/a'],['test/b','missing'],['test/b','test/foreign'],None,['test/b',{}]]:
   with self.assertRaises(Invalid):self.hub.set_focus({'provider':'Codex','order':order})
   self.assertEqual(self.hub.store_path.read_bytes(),before)

 def test_explicit_pin_releases_when_done_idle_or_expired(self):
  for phase in ['completed','idle','expired']:
   self.event('a');self.event('b')
   self.hub.set_focus({'source':'test','session_id':'a'})
   if phase=='expired':self.now+=301;self.event('b')
   else:self.event('a',phase)
   value=self.hub.snapshot();self.assertFalse(value['focus_pinned']);self.assertEqual(value['session_id'],'b')
 def test_explicit_pin_persists_while_actionable(self):
  self.event('a');self.event('b');self.hub.set_focus({'source':'test','session_id':'b'})
  self.event('a','running');self.assertEqual(self.focus(),'b')
  self.hub=StatusHub(Path(self.tmp.name),lambda:self.now)
  self.assertTrue(self.hub.snapshot()['focus_pinned']);self.assertEqual(self.focus(),'b')
  self.event('b','waiting',pending_input=1);self.now+=301;self.event('a')
  self.assertTrue(self.hub.snapshot()['focus_pinned']);self.assertEqual(self.focus(),'b')
 def test_legacy_implicit_pin_migrates_to_auto(self):
  self.event('a');self.event('b');self.hub.set_focus({'source':'test','session_id':'b'})
  path=self.hub.store_path;saved=json.loads(path.read_text());saved.pop('focus_policy_version');path.write_text(json.dumps(saved))
  self.hub=StatusHub(Path(self.tmp.name),lambda:self.now)
  self.assertFalse(self.hub.snapshot()['focus_pinned']);self.assertEqual(self.focus(),'a')

class SignedMoveTests(unittest.TestCase):
 def test_v2_order_target_and_action_signed(self):
  config={'key':'01'*32};r={'seq':2,'provider':0,'generation':4,'pending':True,'id':'02'*16,'action':1,'before':'03'*16};nonce='04'*8
  raw=struct.pack('<8sIBIB16s',bytes.fromhex(nonce),2,0,4,True,bytes.fromhex(r['id']))+bytes([1])+bytes.fromhex(r['before'])
  r['proof']=hmac.digest(bytes.fromhex(config['key']),raw,'sha256').hex();info={'catalog_v':2,'nonce':nonce,'request':r}
  self.assertEqual(bridge.verify_request(info,config),r)
  for field,value in [('action',0),('before','05'*16),('before',''),('id','06'*16)]:
   bad=json.loads(json.dumps(info));bad['request'][field]=value
   with self.assertRaises(ValueError):bridge.verify_request(bad,config)
  bad=json.loads(json.dumps(info));bad['catalog_v']=1
  with self.assertRaises(ValueError):bridge.verify_request(bad,config)

 def test_signed_batch_and_invalid_permutations(self):
  config={'key':'01'*32};nonce='04'*8
  def info(order):
   body=bytes([len(order),*order])+bytes(15-len(order))
   r={'seq':1,'provider':0,'generation':3,'pending':True,'id':'','action':2,'before':body.hex()}
   raw=struct.pack('<8sIBIB16s',bytes.fromhex(nonce),1,0,3,True,bytes(16))+bytes([2])+body
   r['proof']=hmac.digest(bytes.fromhex(config['key']),raw,'sha256').hex()
   return {'catalog_v':2,'nonce':nonce,'request':r}
  good=info([2,0,1]);self.assertEqual(bridge.decode_order(bridge.verify_request(good,config)['before']),[2,0,1])
  for order in [[],[0,0],[1,2],[0,1,2,3,4,5,6,7,8]]:
   with self.assertRaises(ValueError):bridge.verify_request(info(order),config)
  good['request']['before']=info([0,1,2])['request']['before']
  with self.assertRaises(ValueError):bridge.verify_request(good,config)

class RelayMoveTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import asyncio
  asyncio.get_running_loop().set_debug(False)
 async def test_reorder_terminal_row_and_reject_stale_catalog(self):
  for valid in [True,False]:
   now=time.time();entries=[dict(provider='Codex',source='test',session_id=id,phase=phase,source_fresh=True,task_fresh=True,expires_at=now+100,task_expires_at=now+100,priority=i) for i,(id,phase) in enumerate([('a','completed'),('b','running')])]
   payload={'version':1,'hub_alive':True,'sessions':entries,'focus_pinned':False,'focus_provider':'Codex','focused_session':'test/b'}
   relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.sent_at=[100]*3;rows,*_=relay.catalogue(payload,'Codex',now);byid={r['wire_id']:r for r in rows}
   relay.sent_catalogs[(0,1)]={'sent_at':100 if valid else 70,'entries':byid}
   request={'seq':1,'provider':0,'generation':1,'pending':True,'id':bridge.wire_id(entries[0]),'action':1,'before':bridge.wire_id(entries[1])}
   client=Mock();client.write_gatt_char=AsyncMock();info={'catalog_v':2,'nonce':'0'*16,'accepted':0}
   with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=103),patch.object(bridge,'focus_http',return_value=payload) as post:
    await relay.step(client,{'key':'0'*64},payload)
    if valid:post.assert_called_once_with({'provider':'Codex','move':'test/a','before':'test/b'});self.assertEqual(relay.results[0],(1,1))
    else:post.assert_not_called();self.assertEqual(relay.results[0],(1,2))

class BatchRelayTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  import asyncio
  asyncio.get_running_loop().set_debug(False)
 async def test_one_batch_request_and_membership_race(self):
  now=time.time()
  for changed in [False,True]:
   entries=[dict(provider='Codex',source='test',session_id=id,phase='running',source_fresh=True,task_fresh=True,expires_at=now+100,task_expires_at=now+100,priority=i) for i,id in enumerate(['a','b','c'])]
   payload={'version':1,'hub_alive':True,'sessions':entries,'focus_pinned':False,'focus_provider':'Codex','focused_session':'test/a','priority_order':['test/c','test/a','test/b']}
   relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.sent_at=[100]*3;rows,*_=relay.catalogue(payload,'Codex',now)
   relay.sent_catalogs[(0,1)]={'sent_at':100,'entries':{r['wire_id']:r for r in rows}}
   request={'seq':1,'provider':0,'generation':1,'pending':True,'id':'','action':2,'before':(bytes([3,2,0,1])+bytes(12)).hex()}
   if changed:payload['sessions']=entries[:2]
   client=Mock();client.write_gatt_char=AsyncMock();info={'catalog_v':2,'nonce':'0'*16,'accepted':0}
   with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=103),patch.object(bridge,'focus_http',return_value=payload) as post:
    await relay.step(client,{'key':'0'*64},payload)
    if changed:post.assert_not_called();self.assertEqual(relay.results[0],(1,2))
    else:post.assert_called_once_with({'provider':'Codex','order':['test/c','test/a','test/b']});self.assertEqual(relay.results[0],(1,1))

if __name__=='__main__':unittest.main()
