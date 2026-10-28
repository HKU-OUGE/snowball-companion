import concurrent.futures
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins/snowball-companion'
sys.path.insert(0, str(PLUGIN / 'scripts'))
sys.path.insert(0, str(PLUGIN / 'runtime'))
import snowball_hook as hook
from status_hub import StatusHub, HubServer


def payload(event, **kw):
    return dict(session_id='main-session', hook_event_name=event, cwd='/private/project', **kw)


class PluginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / '.config/xueqiu'
        self.root.mkdir(parents=True, mode=0o700)
        (self.root / 'hub-token').write_text('x' * 43)
        (self.root / 'hub-token').chmod(0o600)
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, event, **kw):
        def capture(e, token):
            self.assertEqual(token, 'x' * 43)
            self.events.append(e)
            return {'ok': True}
        return hook.process(event, payload(event, **kw), root=self.root, sender=capture)

    def test_manifest_paths_and_config_are_portable(self):
        m = json.loads((PLUGIN / '.claude-plugin/plugin.json').read_text())
        market = json.loads((ROOT / '.claude-plugin/marketplace.json').read_text())
        self.assertEqual(market['plugins'][0]['name'], m['name'])
        hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text())['hooks']
        self.assertEqual(set(hooks), set(hook.EVENTS))
        self.assertNotIn('PermissionRequest', hooks)
        for event, rows in hooks.items():
            cmd = rows[0]['hooks'][0]
            self.assertEqual(cmd['timeout'], 2)
            self.assertIn('"${CLAUDE_PLUGIN_ROOT}/scripts/snowball_hook.py"', cmd['command'])
            self.assertTrue(cmd['command'].endswith(' ' + event))
            self.assertNotIn('async', cmd)

    def test_lifecycle(self):
        cases = [('SessionStart','idle'), ('UserPromptSubmit','thinking'), ('PreToolUse','running'),
                 ('PostToolUse','thinking'), ('Stop','completed'), ('SessionEnd','idle')]
        for event, phase in cases:
            self.assertEqual(self.send(event, tool_name='Bash', tool_use_id='t')['phase'], phase)

    def test_permission_request_never_enters_waiting(self):
        self.assertIsNone(hook.process('PermissionRequest', payload('PermissionRequest'), root=self.root))
        self.assertEqual(self.send('PreToolUse', tool_name='Bash')['phase'], 'running')
        self.assertEqual(self.send('PostToolUse', tool_name='Bash')['phase'], 'thinking')

    def test_real_permission_notification(self):
        e = self.send('Notification', notification_type='permission_prompt', message='SECRET bash command')
        self.assertEqual(e['phase'], 'waiting')
        self.assertEqual(e['pending_input'], 1)
        self.assertNotIn('SECRET', json.dumps(e))
        self.assertEqual(self.send('PostToolUse', tool_name='Bash')['pending_input'], 0)

    def test_terminal_stop_clears_notification_only_permission(self):
        self.send('Notification',notification_type='permission_prompt')
        e=self.send('Stop')
        self.assertEqual(e['pending_input'],0);self.assertEqual(e['phase'],'completed')

    def test_question_survives_other_tool_and_stop(self):
        a = self.send('PreToolUse', tool_name='AskUserQuestion', tool_use_id='question-a',
                      tool_input={'questions':[{'question':'选择颜色？','options':[{'label':'绿'}]}]})
        qid = a['questions'][0]['id']
        for event in ('PreToolUse','PostToolUse','Stop'):
            b = self.send(event, tool_name='Read', tool_use_id='other')
            self.assertEqual(b['phase'],'waiting')
            self.assertEqual(b['questions'][0]['id'],qid)
        self.assertEqual(self.send('PostToolUse',tool_name='AskUserQuestion',tool_use_id='question-a')['pending_input'],0)

    def test_simultaneous_questions_resolve_individually(self):
        for call in ('a','b'):
            self.send('PreToolUse',tool_name='AskUserQuestion',tool_use_id=call,tool_input={'questions':[{'question':call}]})
        b=self.send('PostToolUse',tool_use_id='a')
        self.assertEqual([q['text'] for q in b['questions']],['b'])

    def test_new_prompt_or_end_clears_old_questions(self):
        for event in ('UserPromptSubmit','SessionEnd'):
            self.send('PreToolUse',tool_name='AskUserQuestion',tool_use_id='q')
            self.assertEqual(self.send(event)['pending_input'],0)

    def test_supplied_answers_do_not_raise_question(self):
        e=self.send('PreToolUse',tool_name='AskUserQuestion',tool_use_id='q',
                    tool_input={'questions':[{'question':'Color?'}],'answers':{'Color?':'green'}})
        self.assertEqual(e['pending_input'],0)

    def test_partial_supplied_answer_preserves_other_question(self):
        e=self.send('PreToolUse',tool_name='AskUserQuestion',tool_use_id='q',
            tool_input={'questions':[{'question':'Color?'},{'question':'Size?'}],'answers':{'Color?':'green'}})
        self.assertEqual([q['text'] for q in e['questions']],['Size?'])

    def test_mcp_wait_and_response(self):
        e=self.send('Notification',notification_type='elicitation_dialog',message='请选择工作目录')
        self.assertEqual(e['phase'],'waiting')
        self.assertEqual(self.send('Notification',notification_type='elicitation_response')['pending_input'],0)

    def test_subagent_cannot_finish_parent(self):
        a=self.send('UserPromptSubmit')
        for event in hook.EVENTS:
            self.assertIsNone(self.send(event,agent_id='child'))
        self.assertEqual(len(self.events),1)

    def test_tool_failure_is_not_turn_failure(self):
        self.assertEqual(self.send('PostToolUseFailure')['phase'],'thinking')
        self.assertEqual(self.send('StopFailure')['phase'],'failed')
        self.assertEqual(self.send('Notification',notification_type='idle_prompt')['phase'],'failed')

    def test_title_and_payload_privacy(self):
        e=self.send('UserPromptSubmit',prompt='SECRET PROMPT',transcript_path='/never/read/SECRET',last_assistant_message='SECRET RESULT')
        self.assertIn('project',e['display_name'])
        self.assertNotIn('SECRET',json.dumps(e))
        self.assertNotIn('/private',json.dumps(e))
        self.assertEqual(e['ttl_seconds'],300)
        self.assertEqual(e['kind'],'task')
        self.assertNotIn('main-session',json.dumps(e))
        self.assertEqual(set(e),{'version','provider','source','session_id','event_id','seq','timestamp','kind','phase','activity_phase','ttl_seconds','display_name','pending_input','questions'})

    def test_long_unicode_question_is_bounded(self):
        e=self.send('PreToolUse',tool_name='AskUserQuestion',tool_use_id='q',tool_input={'questions':[{'question':'雪'*1000+'\x00'}]})
        text=e['questions'][0]['text']
        self.assertLessEqual(len(text),256);self.assertLessEqual(len(text.encode()),768);self.assertTrue(text.endswith('…'))

    def test_mismatch_or_missing_identity_is_not_sent(self):
        self.assertIsNone(hook.process('Stop',payload('PreToolUse'),root=self.root))
        with self.assertRaises(ValueError):hook.process('Stop',{'hook_event_name':'Stop'},root=self.root)

    def test_private_files_and_symlink_rejection(self):
        self.send('SessionStart')
        for p in (self.root/'hook-state'/hook.SOURCE).iterdir():
            self.assertEqual(p.stat().st_mode & 0o777,0o600)
        token=self.root/'hub-token';token.chmod(0o644)
        with self.assertRaises(ValueError):self.send('Stop')
        token.unlink();token.symlink_to(Path(self.tmp.name)/'untrusted')
        with self.assertRaises(OSError):self.send('Stop')

    def test_concurrent_callbacks_serialize(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results=list(pool.map(lambda _:self.send('PreToolUse'),range(12)))
        self.assertEqual(sorted(e['seq'] for e in results),list(range(1,13)))
        self.assertEqual([e['seq'] for e in self.events],list(range(1,13)))

    def test_lock_wait_is_bounded(self):
        self.send('SessionStart');sid=hook.identity(payload('Stop'))
        with open(self.root/'hook-state'/hook.SOURCE/(sid+'.lock'),'r+') as f:
            fcntl.flock(f,fcntl.LOCK_EX);start=time.monotonic()
            with self.assertRaises(TimeoutError):self.send('Stop')
            self.assertLess(time.monotonic()-start,.5)

    def test_failed_delivery_not_replayed(self):
        def fail(*_):raise OSError('offline')
        with self.assertRaises(OSError):hook.process('Stop',payload('Stop'),root=self.root,sender=fail)
        e=self.send('UserPromptSubmit');self.assertEqual(e['seq'],2);self.assertEqual(len(self.events),1)

    def test_cli_empty_output_even_on_failure_or_large_input(self):
        copied=Path(self.tmp.name)/'plugin cache with spaces';shutil.copytree(PLUGIN/'scripts',copied)
        for raw in ('bad-json','x'*(hook.INPUT_LIMIT+1),json.dumps(payload('SessionStart',agent_id='child'))):
            proc=subprocess.run([sys.executable,str(copied/'snowball_hook.py'),'SessionStart'],input=raw,text=True,
                                capture_output=True,timeout=3)
            self.assertEqual((proc.returncode,proc.stdout,proc.stderr),(0,'',''))

    def test_real_isolated_hub_flow_and_expiry(self):
        state=Path(self.tmp.name)/'hub';hub=StatusHub(state,desktop=None)
        server=HubServer(('127.0.0.1',0),hub,'x'*43)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(hook,'HUB',f'http://127.0.0.1:{server.server_port}'):
                def send(event,**kw):return hook.process(event,payload(event,**kw),root=self.root)
                send('UserPromptSubmit');send('PreToolUse',tool_name='AskUserQuestion',tool_use_id='q',tool_input={'questions':[{'question':'准备好了吗？'}]})
                s=hub.snapshot();self.assertEqual(s['state'],'waiting');self.assertEqual(s['questions'][0]['text'],'准备好了吗？')
                send('PostToolUse',tool_use_id='q');send('Stop')
                s=hub.snapshot();self.assertEqual(s['state'],'completed');self.assertEqual(s['pending_input'],0)
                send('UserPromptSubmit')
                hub.clock=lambda:time.time()+301
                self.assertFalse(hub.snapshot()['task_fresh'])
                self.assertEqual(hub.snapshot()['status'],'unknown')
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_wrong_hub_token_rejected_without_state_change(self):
        hub=StatusHub(Path(self.tmp.name)/'hub-denied',desktop=None)
        server=HubServer(('127.0.0.1',0),hub,'y'*43)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(hook,'HUB',f'http://127.0.0.1:{server.server_port}'):
                with self.assertRaises(Exception):hook.process('Stop',payload('Stop'),root=self.root)
            self.assertEqual(hub.snapshot()['sessions'],[])
        finally:
            server.shutdown();server.server_close();worker.join()

    def test_redirect_refused_and_environment_proxy_ignored(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        seen=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*_):pass
            def do_GET(self):
                seen.append(self.path)
                self.send_response(302);self.send_header('Location','/credential-trap');self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            with patch.object(hook,'HUB',f'http://127.0.0.1:{server.server_port}'),patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
                with self.assertRaises(Exception):hook.request('/v1/health','x'*43)
            self.assertEqual(seen,['/v1/health'])
        finally:
            server.shutdown();server.server_close();worker.join()


if __name__=='__main__':unittest.main()
