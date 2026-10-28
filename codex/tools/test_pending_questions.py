"""Offline production-module regressions; never emits into the live hub."""
import importlib.util
import io
import sys
import json
from pathlib import Path
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'companion'))
def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
adapter = load('pending_adapter', ROOT / 'plugins/xueqiu-status/scripts/codex_hook.py')
hubmod = load('pending_hub', ROOT / 'companion/status_hub.py')
bridge = load('pending_bridge', ROOT / 'companion/bridge.py')


def event(name, call=None, **kw):
    return {'hook_event_name': name, 'tool_use_id': call, **kw}

def reply(call, index=0, namespace='request_user_input_async'):
    body = [{'questionItemId': json.dumps([namespace, call, index]),
             'question': 'PRIVATE_QUESTION', 'answer': 'PRIVATE_ANSWER'}]
    return '<send_user_message_question_reply>\n' + json.dumps(body) + '\n</send_user_message_question_reply>'


class DeliveryDiagnosticsTests(unittest.TestCase):
    def test_rejection_is_private_diagnostic_and_next_event_recovers_without_replay(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(adapter, 'session_title', return_value=None):
            root = Path(tmp)
            calls = []
            def rejected(value):
                calls.append(value['seq'])
                raise urllib.error.HTTPError('http://127.0.0.1:4190/v1/events', 409, 'Conflict', {},
                    io.BytesIO(b'{"error":"session_capacity_reached"}'))
            adapter.process_hook(event('UserPromptSubmit', session_id='fixture', prompt='PRIVATE_INPUT'), root, rejected)
            path = root/'hooks'/(adapter.opaque('fixture')+'.json')
            record = json.loads(path.read_text())
            self.assertEqual(record['activity_phase'], 'thinking')
            self.assertEqual(record['last_delivery']['error'], 'session_capacity_reached')
            self.assertEqual(record['last_delivery']['status'], 'rejected')
            self.assertNotIn('PRIVATE_INPUT', path.read_text())
            adapter.process_hook(event('PreToolUse', 'next', session_id='fixture'), root,
                lambda value: calls.append(value['seq']))
            record = json.loads(path.read_text())
            self.assertEqual(record['last_delivery']['status'], 'accepted')
            self.assertEqual(calls, [1, 2])

    def test_unknown_transport_result_is_not_reported_as_accepted(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(adapter, 'session_title', return_value=None):
            def uncertain(_):
                raise OSError('PRIVATE_NETWORK_DETAIL')
            root = Path(tmp)
            adapter.process_hook(event('UserPromptSubmit', session_id='fixture'), root, uncertain)
            record = json.loads((root/'hooks'/(adapter.opaque('fixture')+'.json')).read_text())
            self.assertEqual(record['last_delivery']['status'], 'unconfirmed')
            self.assertNotIn('PRIVATE_NETWORK_DETAIL', json.dumps(record))

class PendingTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
    def apply(self, name, call=None, **kw):
        return adapter.hook_phase(event(name, call, **kw), self.state)
    def ask(self, call='question-1', count=1):
        return self.apply('PreToolUse', call, tool_name='functions.request_user_input_async',
                          tool_input={'questions': [{'question': 'PRIVATE_QUESTION'}] * count})
    def test_post_submission_and_parallel_activity_keep_waiting(self):
        self.assertEqual(self.ask(), 'waiting')
        self.assertEqual(self.apply('PostToolUse', 'question-1', tool_response={'submitted': True}), 'waiting')
        self.assertEqual(self.apply('PreToolUse', 'background'), 'waiting')
        self.assertEqual(self.state['activity_phase'], 'running')
        self.assertEqual(self.apply('PostToolUse', 'background'), 'waiting')
        self.assertEqual(self.apply('Stop'), 'waiting')
        self.assertEqual(self.state['activity_phase'], 'completed')
    def test_ordinary_user_input_does_not_answer_question(self):
        self.ask()
        self.assertEqual(self.apply('UserPromptSubmit', prompt='Continue something else'), 'waiting')
    def test_matching_reply_clears_only_matching_question(self):
        self.ask(count=2)
        self.ask('question-2')
        for call, index, expected in [('unrelated',0,'waiting'),('question-1',0,'waiting'),
                                      ('question-1',0,'waiting'),('question-1',1,'waiting'),
                                      ('question-2',0,'thinking')]:
            self.assertEqual(self.apply('UserPromptSubmit', prompt=reply(call,index)), expected)
    def test_wrong_namespace_index_and_quoted_envelope_are_not_answers(self):
        self.ask()
        for prompt in (reply('question-1',2), reply('question-1',0,'other'),
                       'Please quote: '+reply('question-1'), 'not json'):
            self.assertEqual(self.apply('UserPromptSubmit', prompt=prompt), 'waiting')
    def test_resume_compaction_and_interrupt_preserve_pending(self):
        self.ask()
        for name in ('PreCompact','PostCompact','SessionStart','Interrupt','Stop'):
            self.assertEqual(self.apply(name), 'waiting')
        self.assertEqual(self.apply('SessionEnd'), 'idle')
        self.assertFalse(self.state['pending_questions'])
    def test_explicit_submission_failure_and_cancellation_clear_only_one(self):
        for response in ({'isError':True}, {'cancelled':True}, {'status':'error'},
                         json.dumps({'status':'canceled'})):
            self.state={}
            self.ask()
            self.ask('question-2')
            self.assertEqual(self.apply('PostToolUse','question-1',tool_response=response),'waiting')
            self.assertEqual(self.apply('PostToolUse','question-2',tool_response=response),'thinking')
    def test_unknown_result_or_empty_answers_are_not_resolution(self):
        for response in ({}, {'answers':{}}, {'submitted':True}, 'not json', None):
            self.state={}
            self.ask()
            self.assertEqual(self.apply('PostToolUse','question-1',tool_response=response),'waiting')
    def test_sync_user_input_still_clears_on_own_post(self):
        for tool in ('request_user_input', 'functions.request_user_input'):
            self.state={}
            self.assertEqual(self.apply('PreToolUse','sync',tool_name=tool),'waiting')
            self.assertEqual(self.apply('PostToolUse','other'),'waiting')
            self.assertEqual(self.apply('PostToolUse','sync'),'thinking')
    def test_approval_pipeline_is_not_a_confirmed_user_prompt(self):
        # Current PermissionRequest has no tool id or reviewer in its schema.
        # Unknown routing, auto approval and auto denial must all stay activity.
        for call in (None, 'command'):
            for mode in (None, 'default', 'acceptEdits', 'plan', 'dontAsk', 'bypassPermissions'):
                self.state={}
                self.assertEqual(self.apply('PreToolUse','command',tool_name='Bash'),'running')
                self.assertEqual(self.apply('PermissionRequest',call,tool_name='Bash',
                    permission_mode=mode),'running')
                self.assertEqual(self.state['waiting'],[])
                self.assertEqual(self.state['pending_questions'],{})
                self.assertEqual(self.apply('PostToolUse','command',
                    tool_response={'isError':True,'status':'denied'}),'thinking')
    def test_approval_activity_does_not_clear_real_questions(self):
        self.assertEqual(self.ask(),'waiting')
        self.apply('PostToolUse','question-1',tool_response={'submitted':True})
        self.assertEqual(self.apply('PermissionRequest',tool_name='Bash'),'waiting')
        self.assertEqual(self.apply('PostToolUse','background',
            tool_response={'status':'denied'}),'waiting')
        self.assertEqual(self.apply('UserPromptSubmit',prompt=reply('question-1')),'thinking')
        self.assertEqual(self.apply('PreToolUse','next',tool_name='Bash'),'running')
    def test_permission_failure_text_never_manufactures_attention(self):
        self.assertEqual(self.apply('PreToolUse','command',tool_name='Bash'),'running')
        self.assertEqual(self.apply('PermissionRequest',tool_name='Bash'),'running')
        self.assertEqual(self.apply('PostToolUse','command',
            tool_response='Automatic approval denied. Ask the user for permission.'),'thinking')
        self.assertEqual(self.state['waiting'],[])
        self.assertEqual(self.ask(),'waiting')
    def test_automatic_approval_does_not_preempt_higher_priority_session(self):
        # Exercise the adapter -> hub boundary that falsely focused checker.
        with tempfile.TemporaryDirectory() as temp, patch.object(adapter,'session_title',return_value=None):
            root=Path(temp)
            hub=hubmod.StatusHub(root/'hub')
            def send(session,name,call=None,**kw):
                adapter.process_hook(event(name,call,session_id=session,**kw),root/'adapter',hub.accept)
            send('priority-first','PreToolUse','first-command',tool_name='Bash')
            send('other-session','PreToolUse','other-command',tool_name='Bash')
            send('other-session','PermissionRequest',tool_name='Bash',permission_mode='default')
            self.assertEqual(hub.snapshot()['session_id'],adapter.opaque('priority-first'))
            self.assertEqual(hub.snapshot()['state'],'running')
            send('other-session','PostToolUse','other-command',tool_response={'status':'denied'})
            self.assertEqual(hub.snapshot()['session_id'],adapter.opaque('priority-first'))
            send('other-session','PreToolUse','actual-question',tool_name='request_user_input_async',
                 tool_input={'questions':[{'question':'PRIVATE_QUESTION'}]})
            self.assertEqual(hub.snapshot()['session_id'],adapter.opaque('other-session'))
            self.assertEqual(hub.snapshot()['state'],'waiting')
            send('other-session','UserPromptSubmit',prompt=reply('actual-question'))
            self.assertEqual(hub.snapshot()['session_id'],adapter.opaque('priority-first'))
            self.assertEqual(hub.snapshot()['state'],'running')
    def test_alias_call_id_and_missing_tool_input(self):
        self.assertEqual(adapter.hook_phase({'hook_event_name':'PreToolUse',
            'tool_call_id':'fallback','tool_name':'request_user_input_async'},self.state),'waiting')
        self.assertEqual(self.apply('UserPromptSubmit',prompt=reply('fallback')),'waiting')
    def test_only_authorized_question_titles_cross_boundary_never_answers(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(adapter,'session_title',return_value=None):
            root=Path(temp); sent=[]
            payload=event('PreToolUse','raw-call-secret',session_id='raw-session-secret',
                          tool_name='request_user_input_async',
                          tool_input={'questions':[{'question':'PRIVATE_QUESTION'}]})
            adapter.process_hook(payload,root,sent.append)
            payload=event('UserPromptSubmit',session_id='raw-session-secret',prompt=reply('raw-call-secret'))
            adapter.process_hook(payload,root,sent.append)
            evidence=json.dumps(sent)+''.join(p.read_text() for p in root.rglob('*.json'))
            self.assertIn('PRIVATE_QUESTION', evidence)
            for secret in ('PRIVATE_ANSWER','raw-call-secret','raw-session-secret'):
                self.assertNotIn(secret,evidence)
    def test_reducer_survives_process_restart(self):
        self.ask(count=2)
        self.state=json.loads(json.dumps(self.state))
        self.assertEqual(self.apply('Stop'),'waiting')
        self.assertEqual(self.apply('UserPromptSubmit',prompt=reply('question-1',0)),'waiting')
        self.assertEqual(self.apply('UserPromptSubmit',prompt=reply('question-1',1)),'thinking')
    def test_auto_focus_prioritizes_waiting_but_respects_pin_and_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            now=[1000.0]; hub=hubmod.StatusHub(Path(temp),lambda:now[0])
            def send(session,phase,seq=1):
                hub.accept(dict(version=1,provider='Codex',source='synthetic',session_id=session,
                    event_id=f'{session}-{seq}',seq=seq,timestamp=now[0],kind='task',phase=phase,ttl_seconds=30))
            send('question','waiting'); now[0]+=1; send('background','running')
            self.assertEqual(hub.snapshot()['session_id'],'question')
            hub.set_focus({'source':'synthetic','session_id':'background'})
            now[0]+=1; send('question','waiting',2)
            self.assertEqual(hub.snapshot()['session_id'],'background')
            hub.set_focus({'auto':True})
            self.assertEqual(hub.snapshot()['session_id'],'question')
            now[0]+=31; send('background','running',2)
            self.assertEqual(hub.snapshot()['session_id'],'background')
    def test_pending_does_not_manufacture_freshness(self):
        with tempfile.TemporaryDirectory() as temp:
            now=[1000.0]; hub=hubmod.StatusHub(Path(temp),lambda:now[0])
            hub.accept(dict(version=1,provider='Codex',source='synthetic',session_id='question',
                event_id='one',seq=1,timestamp=now[0],kind='task',phase='waiting',ttl_seconds=300))
            now[0]+=301
            snapshot=hub.snapshot()
            self.assertEqual(snapshot['last_state'],'waiting')
            self.assertFalse(snapshot['task_fresh'])
            self.assertNotIn('state',snapshot)

class DurableAttentionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1000.0
        self.hub = hubmod.StatusHub(self.root / 'hub', lambda: self.now)
    def tearDown(self):
        self.temp.cleanup()
    def send(self, count=2, phase='waiting', seq=1, **extra):
        self.hub.accept(dict(version=1,provider='Codex',source='synthetic',session_id='question',
            event_id=f'event-{seq}',seq=seq,timestamp=self.now,kind='task',phase=phase,
            pending_input=count,ttl_seconds=300,**extra))
    def test_pending_survives_ttl_and_hub_restart_without_fake_activity(self):
        self.send()
        task_at = self.hub.snapshot()['task_updated_at']
        self.now += 3600
        self.hub = hubmod.StatusHub(self.root / 'hub', lambda: self.now)
        snapshot = self.hub.snapshot()
        self.assertEqual(snapshot['pending_input'],2)
        self.assertEqual(snapshot['status'],'attention')
        self.assertEqual(snapshot['state'],'waiting')
        self.assertFalse(snapshot['source_fresh'])
        self.assertFalse(snapshot['task_fresh'])
        self.assertEqual(snapshot['task_updated_at'],task_at)
        target = bridge.parse_source(snapshot,self.now)
        self.assertEqual(target.state,'waiting')
        self.assertEqual(target.task_updated_at,task_at)
        self.assertEqual(target.valid_until,self.now+30)
        rows,total,pinned,focused=bridge.CatalogRelay().catalogue(snapshot,'Codex',self.now)
        self.assertEqual(total,1)
        self.assertTrue(rows[0]['active'])
        self.assertTrue(focused)
        self.hub.set_focus({'source':'synthetic','session_id':'question'})
        self.assertTrue(self.hub.snapshot()['focus_pinned'])
    def test_partial_answer_keeps_attention_all_answered_returns_activity(self):
        self.send(); self.now+=301
        self.send(count=1,seq=2)
        self.assertEqual(bridge.parse_source(self.hub.snapshot(),self.now).state,'waiting')
        self.now+=1; self.send(count=0,phase='running',seq=3)
        snapshot=self.hub.snapshot()
        self.assertEqual(snapshot['pending_input'],0)
        self.assertEqual(snapshot['status'],'known')
        self.assertEqual(bridge.parse_source(snapshot,self.now).state,'running')
        self.now+=301
        with self.assertRaises(bridge.SourceUnavailable): bridge.parse_source(self.hub.snapshot(),self.now)
    def test_dead_hub_does_not_extend_pending_lease(self):
        self.send(); self.now+=301
        snapshot=self.hub.snapshot()
        with self.assertRaises(bridge.SourceUnavailable): bridge.parse_source(snapshot,self.now+31)
        rows,total,*_=bridge.CatalogRelay().catalogue(snapshot,'Codex',self.now+31)
        self.assertEqual(total,0)
        snapshot['hub_alive']=False
        with self.assertRaises(bridge.SourceUnavailable): bridge.parse_source(snapshot,self.now)
    def test_expired_running_cannot_be_labeled_pending_by_bad_input(self):
        for value in (True,-1,4097,'1',1.5):
            with self.subTest(value=value), self.assertRaises(hubmod.Invalid): self.send(count=value)
        with self.assertRaises(hubmod.Invalid): self.send(count=1,phase='running')
    def test_heartbeat_cannot_resolve_or_create_questions(self):
        for count in (0,1):
            with self.assertRaises(hubmod.Invalid):
                self.hub.accept(dict(version=1,provider='Codex',source='synthetic',session_id='question',
                    event_id='heartbeat',seq=1,timestamp=self.now,kind='heartbeat',pending_input=count))
    def test_unannotated_event_does_not_drop_known_pending(self):
        self.send(); self.now+=1
        self.hub.accept(dict(version=1,provider='Codex',source='synthetic',session_id='question',
            event_id='legacy',seq=2,timestamp=self.now,kind='task',phase='running'))
        self.assertEqual(self.hub.snapshot()['state'],'waiting')
        self.assertEqual(self.hub.snapshot()['pending_input'],2)

if __name__=='__main__': unittest.main()

class ReplyRouteTests(unittest.TestCase):
 def test_private_route_preserved_public_options_only_and_removed_on_answer(self):
  with tempfile.TemporaryDirectory() as temp, patch.object(adapter,'session_title',return_value='测试会话'):
   emitted=[];root=Path(temp)
   payload=dict(session_id='thread-real',hook_event_name='PreToolUse',tool_use_id='call-real',tool_name='functions.request_user_input_async',tool_input={'questions':[{'title':'现在怎样继续？','options':['先看方案','继续执行']}]})
   adapter.process_hook(payload,root,emitted.append)
   q=emitted[-1]['questions'][0];self.assertEqual(q['options'],['先看方案','继续执行'])
   self.assertNotIn('thread-real',json.dumps(emitted));self.assertNotIn('call-real',json.dumps(emitted))
   record=json.loads((root/'hooks'/f"{adapter.opaque('thread-real')}.json").read_text());self.assertEqual(record['question_routes'][q['id']]['call'],'call-real')
   adapter.process_hook(dict(session_id='thread-real',hook_event_name='UserPromptSubmit',prompt=reply('call-real')),root,emitted.append)
   self.assertEqual(emitted[-1]['questions'],[])
   record=json.loads((root/'hooks'/f"{adapter.opaque('thread-real')}.json").read_text());self.assertEqual(record['question_routes'],{})
 def test_oversized_option_is_never_silently_truncated_for_submission(self):
  with tempfile.TemporaryDirectory() as temp, patch.object(adapter,'session_title',return_value='测试'):
   emitted=[]
   adapter.process_hook(dict(session_id='thread',hook_event_name='PreToolUse',tool_use_id='call',tool_name='request_user_input_async',tool_input={'questions':[{'title':'选什么？','options':['字'*81]}]}),Path(temp),emitted.append)
   self.assertNotIn('options',emitted[-1]['questions'][0])
