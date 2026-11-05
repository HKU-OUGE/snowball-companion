"""Installer regression tests use fake hosts and temporary homes, never real devices."""
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from console_backend import Backend, SetupError
from console_installer import Installer, validate_choices, specialize_hooks
from console_server import Server


def choices(**features):
    return {'adapters':['codex'],'features':dict(connection=False, autostart=False, speech=False, **features),
            'speech_model':'tiny','commands':{'codex':'','claude':''}}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        folder = Path(self.tmp.name)
        self.backend = Backend(config=folder/'config',state=folder/'state')
        self.calls=[]
        self.backend.configure_feature=lambda name, progress, **kw:self.calls.append((name,kw))
        self.backend.installer.install_adapter=lambda name, data:self.calls.append((name,{}))
        self.backend.installer.available=lambda:{'codex':True,'claude':True,'cursor':True}

    def finish(self):
        self.backend.installer.worker.join(5)
        self.assertFalse(self.backend.installer.worker.is_alive())

    def test_only_selected_features_run_without_agent_or_pet_wait(self):
        data=choices();data['features']['speech']=True
        self.backend.installer.start(data);self.finish()
        self.assertEqual(self.calls,[('environment',{}),('codex',{}),('speech',{'speech_model':'tiny'})])
        status=self.backend.installer.public()
        self.assertTrue(status['complete']);self.assertNotIn('pet',status['steps'])
        self.assertEqual(status['steps']['connection']['state'],'skipped')

    def test_failure_preserves_other_features_and_retry_uses_new_choices(self):
        def install(*_):raise SetupError('host_cli_missing')
        self.backend.installer.install_adapter=install
        data=choices();data['features']['speech']=True
        self.backend.installer.start(data);self.finish()
        self.assertFalse(self.backend.installer.public()['complete'])
        self.assertEqual(self.backend.installer.public()['steps']['speech']['state'],'ready')
        self.backend.installer.install_adapter=lambda *_:None
        data['features']['speech']=False
        self.backend.installer.start(data);self.finish()
        self.assertTrue(self.backend.installer.public()['complete'])

    def test_dependency_failure_does_not_register_adapters_or_services(self):
        def fail(*_,**__):raise SetupError('dependencies_missing')
        self.backend.configure_feature=fail
        self.backend.installer.start(choices());self.finish()
        self.assertEqual(self.calls,[])
        self.assertEqual(self.backend.installer.public()['steps']['codex']['code'],'dependencies_missing')

    def test_restart_never_restarts_mutations_and_marks_inflight_interrupted(self):
        self.backend.installer.start(choices());self.finish()
        self.backend.installer.step('codex','running','checking')
        restored=Installer(self.backend)
        self.assertIsNone(restored.worker)
        self.assertEqual(restored.public()['steps']['codex']['code'],'interrupted')

    def test_concurrent_setup_and_action_cannot_start(self):
        event=threading.Event();entered=threading.Event()
        def wait(*_,**__):entered.set();event.wait(5)
        self.backend.configure_feature=wait
        self.backend.installer.start(choices());entered.wait(2)
        try:
            with self.assertRaises(SetupError):self.backend.setup.start('codex')
            with self.assertRaises(SetupError):self.backend.submit('prepare',{})
            with self.assertRaises(SetupError):self.backend.installer.start(choices())
        finally:event.set();self.finish()

    def test_choices_reject_extra_fields_password_and_shell_commands(self):
        for change in ({'password':'not-accepted'},{'adapters':['unknown']},{'adapters':'codex'},
                       {'features':{'speech':True}},{'speech_model':[]},{'commands':{'codex':'\nwhoami','claude':''}}):
            data=choices();data.update(change)
            with self.assertRaises(SetupError):validate_choices(data)
        data=choices();data['adapters']=[]
        with self.assertRaises(SetupError):validate_choices(data)

    def test_api_requires_local_browser_session_and_no_shell_endpoint(self):
        server=Server(0,self.backend,'local-fixture-token')
        t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port);self.addCleanup(conn.close)
        conn.request('POST','/api/installer',json.dumps(choices()),{'Content-Type':'application/json'})
        r=conn.getresponse();r.read();self.assertEqual(r.status,400);self.assertEqual(self.calls,[])
        headers={'Content-Type':'application/json','X-Snowball-UI':'1','X-Snowball-Session':server.session()}
        conn.request('POST','/api/installer',json.dumps(choices()),headers)
        r=conn.getresponse();r.read();self.assertEqual(r.status,202);self.finish()
        headers['Origin']='https://untrusted.example'
        conn.request('POST','/api/installer',json.dumps(choices()),headers)
        r=conn.getresponse();r.read();self.assertEqual(r.status,400)

    def test_posix_hook_python_path_with_spaces_and_quote_is_one_argument(self):
        import shlex
        folder=Path(self.tmp.name)/'plugin';(folder/'hooks').mkdir(parents=True)
        p=folder/'hooks/hooks.json';p.write_text(json.dumps({'hooks':{'stop':[{'command':'python3 "${CURSOR_PLUGIN_ROOT}/scripts/cursor_hook.py" stop'}]}}))
        python=Path(self.tmp.name)/"a path's 中文/python3"
        specialize_hooks(folder,python)
        command=json.loads(p.read_text())['hooks']['stop'][0]['command']
        self.assertEqual(shlex.split(command),[str(python),'-X','utf8','${CURSOR_PLUGIN_ROOT}/scripts/cursor_hook.py','stop'])

    def test_bundled_interpreter_wins_over_older_global_venv(self):
        from console_backend import interpreter
        base=Path(self.tmp.name)/'package';root=base/'codex/companion';root.mkdir(parents=True)
        (base/'INSTALLER.json').write_text('{}')
        python=base/'python'/('python.exe' if os.name=='nt' else 'bin/python3')
        python.parent.mkdir(parents=True);python.touch()
        self.assertEqual(interpreter(root),str(python))

    def test_fresh_device_only_install_does_not_enable_codex_observation(self):
        from status_hub import codex_observation_enabled
        data=choices();data['adapters']=[];data['features']['connection']=True
        self.backend.installer.start(data);self.finish()
        self.assertFalse(codex_observation_enabled(self.backend.config/'companion-hosts.json'))
        data['adapters']=['codex'];self.backend.installer.start(data);self.finish()
        self.assertTrue(codex_observation_enabled(self.backend.config/'companion-hosts.json'))
        data['adapters']=[];self.backend.installer.start(data);self.finish()
        self.assertTrue(codex_observation_enabled(self.backend.config/'companion-hosts.json'))

    def test_loopback_services_never_resolve_dns(self):
        from numeric_http import ThreadingHTTPServer
        from http.server import BaseHTTPRequestHandler
        with patch('socket.getfqdn',side_effect=AssertionError('unexpected DNS')):
            s=ThreadingHTTPServer(('127.0.0.1',0),BaseHTTPRequestHandler)
            try:self.assertGreater(s.server_port,0)
            finally:s.server_close()

    def test_mac_login_uses_app_with_bluetooth_privacy_description(self):
        import plistlib
        from console_login import mac_app, mac_arguments
        with patch('pathlib.Path.home',return_value=Path(self.tmp.name)):
            app=mac_app(self.backend.root)
            value=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            self.assertTrue(value['NSBluetoothAlwaysUsageDescription'])
            self.assertTrue(value['NSLocalNetworkUsageDescription'])
            self.assertIn(str(app),mac_arguments())
            self.assertIn('--background',mac_arguments())
            self.assertEqual(mac_app(self.backend.root),app)


if __name__=='__main__':unittest.main()
