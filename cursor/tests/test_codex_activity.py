import sys,time
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from codex_activity import CodexActivity
from ota_idle import idle_evidence

def test_directory_fills_capped_sidebar_and_unloaded_is_runtime_idle():
 d=Mock();d.rows={'codex-hooks/a':{'thread':'a'},'codex-hooks/b':{'thread':'b'}};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 t.call.side_effect=[{'schemaVersion':4,'threads':[{'id':'a','kind':'codex','hostId':'local','status':'idle'}],'pinnedThreads':[]},
                     {'thread':{'id':'b','hostId':'local','status':{'type':'notLoaded'}}}]
 a=CodexActivity(t,lambda:1000);a.refresh(d)
 assert a.value['complete'] and not a.value['any_active']
 assert a.value['sessions']=={'codex-hooks/a':'idle','codex-hooks/b':'notLoaded'}
 assert t.call.call_args_list[1].args[0]=='read_thread'

def test_missing_or_unknown_runtime_cannot_claim_coverage():
 d=Mock();d.rows={'codex-hooks/a':{'thread':'a'}};d.fresh.return_value=True
 for state in ['future-status',None]:
  t=Mock();t.available.return_value=True;t.call.return_value={'schemaVersion':4,'threads':[{'id':'a','kind':'codex','hostId':'local','status':state}],'pinnedThreads':[]}
  a=CodexActivity(t,lambda:1000);a.refresh(d);assert not a.value['complete']

def test_runtime_overrides_expired_history_but_questions_and_new_hooks_block():
 s={'version':1,'hub_alive':True,'directory_available':True,'generated_at':1000,'sessions':[
  {'provider':'Codex','source':'codex-hooks','session_id':'a','phase':'idle','source_fresh':False,'pending_total':0,'pending_input':0}],
  'activity_coverage':{'scope':'codex-desktop-local','complete':True,'observed_at':1000,'expires_at':1012,'any_active':False,'sessions':{'codex-hooks/a':'notLoaded'}}}
 assert idle_evidence(s,1001)
 row=s['sessions'][0];row.update(pending_total=1,questions=[{'status':'ignored'}]);assert idle_evidence(s,1001)
 row['questions']=[{'status':'snoozed'}];assert not idle_evidence(s,1001)
 row.update(pending_total=0,questions=[],phase='thinking',task_updated_at=1001);assert not idle_evidence(s,1001)
 row.update(task_updated_at=999);assert idle_evidence(s,1001)
 s['activity_coverage']['any_active']=True;assert not idle_evidence(s,1001)

def test_live_work_veto_never_reads_desktop_history():
 t=Mock();d=Mock();a=CodexActivity(t,lambda:1000)
 a.refresh(d,{'a':{'provider':'Codex','phase':'running','expires_at':1100,'task_expires_at':1100}})
 assert a.value['reason']=='live_codex_activity' and not a.value['complete']
 t.call.assert_not_called();d.fresh.assert_not_called()

def test_unchanged_unloaded_directory_entry_is_not_read_repeatedly():
 now=[1000];d=Mock();d.rows={'codex-hooks/a':{'thread':'a','updated_at':10}};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 listing={'schemaVersion':4,'threads':[],'pinnedThreads':[]}
 t.call.side_effect=[listing,{'thread':{'id':'a','hostId':'local','status':{'type':'notLoaded'}}},listing]
 a=CodexActivity(t,lambda:now[0]);a.refresh(d);now[0]+=16;a.refresh(d)
 assert a.value['complete']
 assert [c.args[0] for c in t.call.call_args_list]==['list_threads','read_thread','list_threads']

def test_resume_during_inventory_revokes_idle_observation():
 d=Mock();d.rows={'codex-hooks/a':{'thread':'a','updated_at':10}};d.fresh.return_value=True
 def changed(**kwargs):d.rows['codex-hooks/a']['updated_at']=11
 d.refresh.side_effect=changed
 t=Mock();t.available.return_value=True
 t.call.return_value={'schemaVersion':4,'threads':[{'id':'a','kind':'codex','hostId':'local','status':'idle'}],'pinnedThreads':[]}
 a=CodexActivity(t,lambda:1000);a.refresh(d)
 assert not a.value['complete'] and a.value['reason']=='inventory_changed'

def test_cold_inventory_warms_two_per_pass_without_claiming_partial_coverage():
 now=[1000];d=Mock();d.rows={f'codex-hooks/{i}':{'thread':str(i),'updated_at':10} for i in range(5)};d.fresh.return_value=True
 t=Mock();t.available.return_value=True;reads=[]
 def call(tool,args,*unused):
  if tool=='list_threads':return {'schemaVersion':4,'threads':[],'pinnedThreads':[]}
  reads.append(args['threadId']);return {'thread':{'id':args['threadId'],'hostId':'local','status':{'type':'notLoaded'}}}
 t.call.side_effect=call;a=CodexActivity(t,lambda:now[0])
 a.refresh(d);assert reads==['0','1'] and not a.value['complete'] and a.value['remaining']==3
 now[0]+=16;a.refresh(d);assert reads==['0','1','2','3'] and not a.value['complete'] and a.value['remaining']==1
 now[0]+=16;a.refresh(d);assert reads==['0','1','2','3','4'] and a.value['complete'] and not a.value['any_active']
 now[0]+=16;a.refresh(d);assert len(reads)==5 and a.value['complete']

def test_partial_cache_is_not_committed_when_directory_changes_during_read():
 now=[1000];d=Mock();d.rows={f'codex-hooks/{i}':{'thread':str(i),'updated_at':10} for i in range(3)};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 def call(tool,args,*unused):
  if tool=='list_threads':return {'schemaVersion':4,'threads':[],'pinnedThreads':[]}
  return {'thread':{'id':args['threadId'],'hostId':'local','status':{'type':'notLoaded'}}}
 t.call.side_effect=call
 d.refresh.side_effect=lambda **unused:d.rows['codex-hooks/0'].update(updated_at=11)
 a=CodexActivity(t,lambda:now[0]);a.refresh(d)
 assert not a.value['complete'] and a.value['reason']=='inventory_changed' and not a.cached
 d.refresh.side_effect=None;now[0]+=16;a.refresh(d)
 assert len([c for c in t.call.call_args_list if c.args[0]=='read_thread'])==4
 assert not a.value['complete'] and a.value['remaining']==1

def test_changed_cached_revision_requires_a_new_cold_read():
 now=[1000];d=Mock();d.rows={'codex-hooks/a':{'thread':'a','updated_at':10}};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 def call(tool,args,*unused):
  if tool=='list_threads':return {'schemaVersion':4,'threads':[],'pinnedThreads':[]}
  return {'thread':{'id':'a','hostId':'local','status':{'type':'notLoaded'}}}
 t.call.side_effect=call;a=CodexActivity(t,lambda:now[0]);a.refresh(d)
 d.rows['codex-hooks/a']['updated_at']=11;now[0]+=16;a.refresh(d)
 assert a.value['complete'] and len([c for c in t.call.call_args_list if c.args[0]=='read_thread'])==2

def test_visible_runtime_work_prevents_all_cold_history_reads():
 d=Mock();d.rows={'codex-hooks/a':{'thread':'a','updated_at':10}};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 t.call.return_value={'schemaVersion':4,'threads':[{'id':'other','kind':'codex','hostId':'local','status':'active'}],'pinnedThreads':[]}
 a=CodexActivity(t,lambda:1000);a.refresh(d)
 assert not a.value['complete'] and a.value['reason']=='runtime_codex_activity'
 assert [c.args[0] for c in t.call.call_args_list]==['list_threads']

def test_slow_cold_read_stops_the_batch_and_expired_pass_cannot_cache():
 now=[1000];d=Mock();d.rows={f'codex-hooks/{i}':{'thread':str(i),'updated_at':10} for i in range(3)};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 def call(tool,args,*unused):
  if tool=='list_threads':return {'schemaVersion':4,'threads':[],'pinnedThreads':[]}
  now[0]+=21;return {'thread':{'id':args['threadId'],'hostId':'local','status':{'type':'notLoaded'}}}
 t.call.side_effect=call;a=CodexActivity(t,lambda:now[0]);a.refresh(d)
 assert not a.value['complete'] and a.value['reason']=='slow_runtime_inventory' and not a.cached
 assert len([c for c in t.call.call_args_list if c.args[0]=='read_thread'])==1

def test_live_hook_veto_after_partial_warmup_does_not_read_or_finish_inventory():
 now=[1000];d=Mock();d.rows={f'codex-hooks/{i}':{'thread':str(i),'updated_at':10} for i in range(3)};d.fresh.return_value=True
 t=Mock();t.available.return_value=True
 def call(tool,args,*unused):
  if tool=='list_threads':return {'schemaVersion':4,'threads':[],'pinnedThreads':[]}
  return {'thread':{'id':args['threadId'],'hostId':'local','status':{'type':'notLoaded'}}}
 t.call.side_effect=call;a=CodexActivity(t,lambda:now[0]);a.refresh(d)
 before=t.call.call_count;now[0]+=16
 a.refresh(d,{'a':{'provider':'Codex','phase':'waiting','expires_at':1100,'task_expires_at':1100}})
 assert not a.value['complete'] and a.value['reason']=='live_codex_activity' and t.call.call_count==before

def test_codex_coverage_survives_fifteen_second_poll_interval_but_expires_at_twenty():
 s={'version':1,'hub_alive':True,'directory_available':True,'generated_at':1016,'sessions':[
  {'provider':'Codex','source':'codex-hooks','session_id':'a','phase':'idle','pending_total':0,'pending_input':0}],
  'activity_coverage':{'scope':'codex-desktop-local','complete':True,'observed_at':1000,
   'expires_at':1020,'any_active':False,'sessions':{'codex-hooks/a':'notLoaded'}}}
 assert idle_evidence(s,1016)
 s['generated_at']=1019.9;assert idle_evidence(s,1019.9)
 s['generated_at']=1020;assert not idle_evidence(s,1020)
 # Extending a supplied expiry cannot bypass the independent20-second age bound.
 s['activity_coverage']['expires_at']=1040;assert not idle_evidence(s,1020)
 # Ordinary hub snapshots still expire after12seconds, even with fresh coverage.
 s['generated_at']=1004;s['activity_coverage']['observed_at']=1015
 assert not idle_evidence(s,1016)
