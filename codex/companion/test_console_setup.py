"""Isolated automatic setup, trust boundaries and persistence; never touches a real account/device."""
import contextlib
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from console_backend import Backend, SetupError
from console_server import Server
from console_setup import SetupPlan
from private_files import private_write


class AutomaticSetup(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.backend=Backend(self.root/'runtime',self.root/'config',self.root/'state')
        self.backend.pet_catalog=lambda:[{'id':'snowball','name':'雪球'}]
        self.calls=[]
        self.backend.configure_feature=lambda name,progress:self.calls.append(name)

    def finished(self):
        if self.backend.setup.worker:self.backend.setup.worker.join(4)
        self.assertFalse(self.backend.setup.public().get('local_running'))

    def test_start_runs_local_setup_once_and_waits_for_real_agent_pet_receipt(self):
        entered,release=threading.Event(),threading.Event()
        def work(name,progress):
            self.calls.append(name)
            if name=='environment':entered.set();release.wait(3)
        self.backend.configure_feature=work
        attach=self.backend.setup.agent({'operation':'attach','adapter':'codex'})
        self.assertTrue(entered.wait(1))
        second=self.backend.setup.start('codex',agent=True)
        self.assertEqual(attach['run'],second['id']);release.set();self.finished()
        self.assertEqual(self.calls,['environment','autostart','connection','speech'])
        self.assertFalse(self.backend.setup.public()['complete'])
        self.assertNotIn(attach['grant'],json.dumps(self.backend.setup.public()))
        envelope={'run':attach['run'],'grant':attach['grant'],'operation':'pet','character':'snowball'}
        with self.assertRaises(SetupError):self.backend.setup.agent(dict(envelope,state='verified'))
        self.backend.setup.agent(dict(envelope,state='working'))
        self.backend.setup.agent(dict(envelope,state='verified'))
        self.assertTrue(self.backend.setup.public()['complete'])
        with self.assertRaises(SetupError):self.backend.setup.agent(dict(envelope,grant='invalid',state='verified'))

    def test_restart_does_not_autorun_or_report_interrupted_work_as_success(self):
        self.backend.setup.start('codex');self.finished()
        self.backend.setup.set_step('speech','running','speech_model')
        self.backend.setup.set_step('pet','running','pet_working')
        restored=SetupPlan(self.backend)
        self.assertIsNone(restored.worker)
        self.assertEqual(restored.public()['steps']['speech']['state'],'failed')
        self.assertEqual(restored.public()['steps']['pet']['state'],'needs_user')
        self.assertFalse(restored.public()['complete'])

    def test_failed_feature_does_not_block_other_independent_features(self):
        def work(name,progress):
            self.calls.append(name)
            if name=='autostart':raise SetupError('autostart_conflict')
        self.backend.configure_feature=work
        self.backend.setup.start('codex');self.finished()
        state=self.backend.setup.public()['steps']
        self.assertEqual(state['autostart']['state'],'failed')
        self.assertEqual(state['speech']['state'],'ready')
        self.assertEqual(state['connection']['state'],'ready')

    def test_environment_failure_blocks_dependent_work_then_retry_resumes(self):
        self.backend.configure_feature=lambda *args:(_ for _ in ()).throw(SetupError('dependencies_missing'))
        self.backend.setup.start('codex');self.finished()
        self.assertEqual(self.backend.setup.public()['steps']['speech']['code'],'dependencies_missing')
        self.backend.configure_feature=lambda name,progress:self.calls.append(name)
        self.backend.setup.edit('retry',name='environment');self.finished()
        self.assertEqual(self.calls,['environment','autostart','connection','speech'])

    def test_character_change_invalidates_completed_pet_and_cannot_change_inflight_choice(self):
        attach=self.backend.setup.agent({'operation':'attach','adapter':'codex'});self.finished()
        self.backend.setup.edit('character',character='snowball')
        envelope={'run':attach['run'],'grant':attach['grant'],'operation':'pet','character':'snowball'}
        self.backend.setup.agent(dict(envelope,state='working'))
        with self.assertRaises(SetupError):self.backend.setup.edit('character',character='auto')
        self.backend.setup.agent(dict(envelope,state='verified'))
        self.backend.setup.edit('character',character='auto')
        self.assertEqual(self.backend.setup.public()['steps']['pet']['state'],'pending')

    def test_speech_reuses_running_model_without_downloading(self):
        self.backend.setup.start('codex');self.finished()
        self.backend.speech_status=lambda:{'ready':True,'profile':'small'}
        with patch('console_backend.subprocess.Popen') as process:
            Backend.configure_feature(self.backend,'speech',lambda stage:None)
        process.assert_not_called()

    def test_non_codex_setup_never_claims_account_pet_support(self):
        self.backend.setup.start('claude',agent=True);self.finished()
        self.assertEqual(self.backend.setup.public()['steps']['pet']['state'],'unsupported')

    def test_browser_cannot_forge_agent_verification(self):
        server=Server(0,self.backend,'fixture-launcher-token')
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        self.addCleanup(connection.close)
        session=server.session()
        headers={'Content-Type':'application/json','X-Snowball-UI':'1','X-Snowball-Session':session}
        connection.request('POST','/agent/setup',json.dumps({'operation':'attach','adapter':'codex'}),headers)
        response=connection.getresponse();response.read();self.assertNotEqual(response.status,200)
        self.assertIsNone(self.backend.setup.value)
        connection.request('POST','/api/setup',json.dumps({'operation':'pet','state':'verified'}),headers)
        response=connection.getresponse();response.read();self.assertEqual(response.status,400)
        connection.request('POST','/agent/setup',json.dumps({'operation':'attach','adapter':'codex'}),{'Content-Type':'application/json','Authorization':'Bearer fixture-launcher-token'})
        response=connection.getresponse();body=json.loads(response.read());self.assertEqual(response.status,200)
        self.finished();self.assertTrue(body['grant'])
        connection.request('POST','/api/setup',json.dumps({'operation':'retry','feature':'pet'}),headers)
        response=connection.getresponse();public=response.read();self.assertEqual(response.status,202)
        self.assertNotIn(body['grant'].encode(),public)


class SpeechReload(unittest.TestCase):
    def test_profile_table_is_multilingual_pinned_and_defaults_to_smaller_base(self):
        from speech_profiles import PROFILES,profile_for
        self.assertEqual(set(PROFILES),{'tiny','base','small'})
        self.assertLess(PROFILES['base']['download_mb'],PROFILES['small']['download_mb']/2)
        for profile in PROFILES.values():
            self.assertRegex(profile['revision'],r'^[0-9a-f]{40}$')
            self.assertNotIn('.en',profile['repo'])
        self.assertEqual(profile_for({'model':'x/'+PROFILES['small']['revision']}),'small')

    def test_reload_requires_auth_and_does_not_interrupt_active_recognizer(self):
        import speech_service as speech
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'speech.json';config.write_text(json.dumps({'model':tmp,'profile':'base'}))
            class Fake:
                profile='small'
                def __init__(self,*a):self.lock=threading.Lock();self.profile=a[2] if len(a)>2 else 'small'
            previous=Fake();server=speech.ThreadingHTTPServer(('127.0.0.1',0),speech.Handler)
            server.token='test';server.recognizer=previous;server.reload_lock=threading.Lock();server.changing=False;server.config_path=config
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
            conn=http.client.HTTPConnection('127.0.0.1',server.server_port);self.addCleanup(conn.close)
            def request(token):
                conn.request('POST','/v1/reload',json.dumps({'reload':True}),{'Authorization':'Bearer '+token,'Host':'127.0.0.1:4191','Content-Type':'application/json'})
                r=conn.getresponse();r.read();return r.status
            self.assertEqual(request('wrong'),403)
            previous.lock.acquire()
            with patch('speech_service.Recognizer',Fake):
                self.assertEqual(request('test'),409)
                self.assertIs(server.recognizer,previous)
                previous.lock.release()
                self.assertEqual(request('test'),200)
                self.assertEqual(server.recognizer.profile,'base')
            self.assertFalse(server.changing)


class ResourcePolicyUpgrade(unittest.TestCase):
    def setUp(self):
        import console_upgrade
        self.module=console_upgrade
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.runtime=self.root/'runtime';self.runtime.mkdir()
        (self.runtime/'console_server.py').write_text('# fixture\n')
        private_write(self.root/'server.json',json.dumps({'port':12345,'token':'fixture-'+'x'*32,'runtime':str(self.runtime)}).encode())

    def test_current_policy_reuses_existing_console(self):
        with patch.object(self.module,'rpc',return_value={'automatic_setup':True,'resource_source_policy':'sd-small-network-large-v1'}),patch.object(self.module,'stop_owned') as stop:
            self.module.ensure_setup_console(self.root,self.runtime)
        stop.assert_not_called()

    def test_busy_old_console_is_preserved_before_policy_upgrade(self):
        from unittest.mock import Mock
        connection=Mock();response=Mock(status=200)
        response.read.return_value=b'{"session":"fixture"}';connection.getresponse.return_value=response
        replies=[{'app':'snowball-console','version':'0.5.7','automatic_setup':True},{'ticket':'fixture'},
                 {'setup':{'local_running':True},'job':None}]
        with patch.object(self.module,'rpc',side_effect=replies),patch.object(self.module.http.client,'HTTPConnection',return_value=connection),patch.object(self.module,'stop_owned') as stop:
            with self.assertRaisesRegex(RuntimeError,'console_busy_wait_for_current_action'):
                self.module.ensure_setup_console(self.root,self.runtime)
        stop.assert_not_called()

    def test_unknown_newer_console_is_not_downgraded(self):
        with patch.object(self.module,'rpc',return_value={'app':'snowball-console','version':'0.9.0','automatic_setup':True}),patch.object(self.module,'stop_owned') as stop:
            with self.assertRaisesRegex(RuntimeError,'console_restart_required'):
                self.module.ensure_setup_console(self.root,self.runtime)
        stop.assert_not_called()


if __name__=='__main__':unittest.main()
