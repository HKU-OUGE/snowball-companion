import ctypes,json,sqlite3,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from desktop_directory import DesktopDirectory,key
from codex_desktop import CodexDesktop,DeliveryError
from status_hub import StatusHub,Invalid
from bridge import CatalogRelay,decode_order
from app_tools import AppTools,AppToolError
from speech_relay import bound_target

class SidebarDirectory(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name);self.root=self.home/'.codex';self.root.mkdir()
  self.db=sqlite3.connect(self.root/'state_1.sqlite');self.addCleanup(self.db.close)
  self.db.execute('CREATE TABLE threads(id TEXT,name TEXT,source TEXT,archived INTEGER,rollout_path TEXT,cwd TEXT,thread_source TEXT,agent_path TEXT,updated_at INTEGER,project_id TEXT)')
 def add(self,i,**kw):
  row=dict(id=f'thread-{i}',name=f'会话{i}',source='vscode',archived=0,rollout_path=str(self.root/'sessions'/f'{i}.jsonl'),cwd='/example/project',thread_source='user',agent_path=None,updated_at=i,project_id=None);row.update(kw)
  self.db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?,?,?,?,?)',list(row.values()));self.db.commit();return row
 def test_inventory_keeps_idle_history_and_excludes_internal_archived(self):
  for i in range(23):self.add(i)
  self.add(40,archived=1);self.add(41,source='{"subagent":{}}');self.add(42,agent_path='/root/worker');self.add(43,thread_source='ambient_suggestion_task')
  directory=DesktopDirectory(self.home);directory.refresh();self.assertEqual(len(directory.rows),23);self.assertTrue(directory.fresh())
  self.db.execute("UPDATE threads SET archived=1 WHERE id='thread-1'");self.db.commit();self.assertIsNone(directory.resolve(key('thread-1')))
 def test_all_directory_rows_do_not_fabricate_task_activity(self):
  for i in range(23):self.add(i)
  desktop=CodexDesktop(self.home);desktop.directory.refresh();desktop.available=lambda:True;desktop.app_tools=Mock();desktop.app_tools.available.return_value=True
  hub=StatusHub(self.home/'hub',desktop=desktop);hub.sync_directory();snapshot=hub.snapshot()
  self.assertEqual(len(snapshot['sessions']),23);self.assertEqual(snapshot['state'],'idle');self.assertFalse(snapshot['source_fresh']);self.assertTrue(snapshot['can_create'])
  self.assertTrue(all(not r['task_fresh'] and r['sidebar_visible'] for r in snapshot['sessions']))
  relay=CatalogRelay();relay.limit=64;rows,*_=relay.catalogue(snapshot,'Codex',time.time());self.assertEqual(len(rows),23);self.assertTrue(all(r['phase']=='idle' and not r['active'] for r in rows))
  self.assertEqual(relay.catalogue({**snapshot,'generated_at':time.time()-60},'Codex',time.time())[0],[])
 def test_sidebar_inventory_does_not_evict_task_records_at_capacity(self):
  for i in range(5):self.add(i)
  desktop=CodexDesktop(self.home);desktop.directory.refresh();desktop.available=lambda:True;desktop.app_tools=Mock();desktop.app_tools.available.return_value=True
  with patch('status_hub.MAX_SESSIONS',3):
   hub=StatusHub(self.home/'hub',desktop=desktop)
   for i in range(3):hub.accept({'version':1,'provider':'Codex','source':'test','session_id':str(i),'seq':1,'event_id':str(i),'timestamp':time.time(),'kind':'task','phase':'running'})
   hub.sync_directory();self.assertEqual(len(hub.records),8)
   self.assertTrue(all('test/'+str(i) in hub.records for i in range(3)))
   self.assertEqual(sum(r.get('catalog_only',False) for r in hub.records.values()),5)
   self.assertEqual(hub.snapshot()['state'],'running')

 def test_idle_unloaded_chat_route_does_not_need_a_hook_record(self):
  self.add(1);desktop=CodexDesktop(self.home);route=desktop.instruction_route(key('thread-1'));self.assertEqual(route,{'thread':'thread-1','activity':'idle'})
  calls=[];desktop.app_tools=Mock();desktop.app_tools.available.return_value=True;desktop.app_tools.call.return_value={'status':'queued','threadId':'thread-1'}
  desktop.deliver_instruction(key('thread-1'),'新增一项任务','a'*32)
  args=desktop.app_tools.call.call_args.args;self.assertEqual(args[0],'send_message_to_thread');self.assertEqual(args[1],{'threadId':'thread-1','hostId':'local','prompt':'新增一项任务'})
  self.db.execute('UPDATE threads SET archived=1');self.db.commit()
  with self.assertRaises(DeliveryError):desktop.deliver_instruction(key('thread-1'),'不能发给归档会话','b'*32)
  self.assertEqual(desktop.app_tools.call.call_count,1)
 def test_creation_uses_app_defaults_and_no_worktree(self):
  self.add(1);desktop=CodexDesktop(self.home);desktop.app_tools=Mock();desktop.app_tools.call.side_effect=[{'projects':[]},{'threadId':'new-id','hostId':'local'}]
  result=desktop.create_instruction('只做已确认的任务','b'*32);self.assertEqual(result['thread_id'],'new-id')
  self.assertEqual(desktop.app_tools.call.call_args_list[0].args[0],'list_projects')
  self.assertEqual(desktop.app_tools.call.call_args_list[1].args[1],{'prompt':'只做已确认的任务','target':{'type':'projectless'}})

class ConfirmedCreation(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.desktop=Mock();self.desktop.summaries={};self.desktop.available.return_value=True;self.desktop.app_tools.available.return_value=True
  self.desktop.create_instruction.return_value={'status':'accepted','thread_id':'created','creation_pending':False}
  self.hub=StatusHub(Path(self.tmp.name),desktop=self.desktop);self.req={'text':'帮我整理新任务','request_id':'c'*32}
 def test_opening_snapshot_never_creates_and_confirm_is_idempotent(self):
  self.hub.snapshot();self.desktop.create_instruction.assert_not_called()
  self.assertEqual(self.hub.create_session(self.req)['delivery'],'accepted');self.hub.create_session(self.req);self.desktop.create_instruction.assert_called_once()
  self.assertNotIn(self.req['text'],self.hub.message_path.read_text())
  with self.assertRaises(Invalid):self.hub.create_session({**self.req,'text':'different'})
 def test_unknown_outcome_is_persisted_and_never_replayed(self):
  self.desktop.create_instruction.side_effect=DeliveryError('timeout',True)
  self.assertEqual(self.hub.create_session(self.req)['delivery'],'unconfirmed')
  restored=StatusHub(Path(self.tmp.name),desktop=self.desktop)
  self.assertEqual(restored.create_session(self.req)['delivery'],'unconfirmed');self.desktop.create_instruction.assert_called_once()
 def test_rejects_malformed_or_disabled_creation_before_dispatch(self):
  for value in [{**self.req,'model':'override'},{**self.req,'text':'\0'},{**self.req,'request_id':'bad'}]:
   with self.assertRaises(Invalid):self.hub.create_session(value)
  self.desktop.app_tools.available.return_value=False
  with self.assertRaises(Invalid):self.hub.create_session(self.req)
  self.desktop.create_instruction.assert_not_called()
 def test_voice_creation_is_explicit_bound_intent(self):
  meta={'sid':'0'*32,'qid':''};payload={'hub_alive':True,'generated_at':time.time(),'can_create':True,'sessions':[]}
  self.assertEqual(bound_target(payload,meta),'new-independent-session')
  for value in [{**payload,'can_create':False},{**payload,'generated_at':time.time()-30}]:
   with self.assertRaises(ValueError):bound_target(value,meta)

class ExpandedInfo(unittest.TestCase):
 def test_maximum_packed_order_and_three_hmacs_fit_attribute(self):
  root=Path(__file__).parents[1]
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'test.c').write_text('#include "ble_info_wire.h"\nint encode(char*out){char d[24],n[17],proof[65],before[99],pet[32];memset(d,\'d\',23);d[23]=0;memset(n,\'a\',16);n[16]=0;memset(proof,\'a\',64);proof[64]=0;memset(before,\'0\',98);before[98]=0;memset(pet,\'p\',31);pet[31]=0;return xq_info_format(out,513,d,n,UINT32_MAX,UINT32_MAX,2,5,proof,UINT32_MAX,2,UINT32_MAX,true,"",12,before,proof,pet,UINT32_MAX,255,proof);}')
   subprocess.run(['cc','-shared','-fPIC','-I',str(root/'main'),str(p/'test.c'),'-o',str(p/'test.so')],check=True)
   lib=ctypes.CDLL(str(p/'test.so'));out=ctypes.create_string_buffer(513);size=lib.encode(out)
   self.assertGreater(size,0);self.assertLessEqual(size,512);self.assertEqual(json.loads(out.value)['v'],5)
