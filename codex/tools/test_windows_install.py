"""Isolated Windows installation tests; no live plugin, task, Hub or USB changes."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS=Path(__file__).resolve().parent
sys.path.insert(0,str(TOOLS))
from configure_windows_plugin import configure, windows_command
spec=importlib.util.spec_from_file_location('windows_install_under_test',TOOLS/'install_plugin.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class WindowsInstall(unittest.TestCase):
    def interpreter(self, folder):
        if sys.platform=='win32':return Path(sys.executable)
        # Command generation validates an existing .exe path; no fixture is executed.
        path=Path(folder)/'Python 中文'/ 'python.exe';path.parent.mkdir(exist_ok=True)
        path.write_bytes(b'fixture');return path
    def fixture(self, root):
        (root/'.codex-plugin').mkdir(parents=True)
        (root/'.codex-plugin/plugin.json').write_text(json.dumps({'name':'xueqiu-status'}))
        (root/'hooks').mkdir()
        hook={'type':'command','command':'python3 "${PLUGIN_ROOT}/scripts/codex_hook.py" hook','timeout':2}
        (root/'hooks/hooks.json').write_text(json.dumps({'hooks':{'SessionStart':[{'hooks':[hook]}]}}))

    def test_native_command_preserves_event_timeout_and_other_plugin_trust(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'plugin with spaces';self.fixture(root)
            trust=Path(folder)/'trust';trust.write_bytes(b'untouched')
            self.assertEqual(configure(root,self.interpreter(folder)),1)
            value=json.loads((root/'hooks/hooks.json').read_text())['hooks']['SessionStart'][0]['hooks'][0]
            self.assertTrue(value['commandWindows'].startswith('powershell.exe '));self.assertEqual(value['timeout'],2)
            self.assertIn('${PLUGIN_ROOT}',value['command']);self.assertEqual(trust.read_bytes(),b'untouched')

    def test_unknown_hook_rejected_before_replacing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);self.fixture(root)
            target=root/'hooks/hooks.json';value=json.loads(target.read_text())
            value['hooks']['SessionStart'][0]['hooks'][0]['command']='unrelated command'
            target.write_text(json.dumps(value));before=target.read_bytes()
            with self.assertRaisesRegex(ValueError,'Unexpected|Unsupported|Unknown|hook|Hook'):configure(root,self.interpreter(folder))
            self.assertEqual(target.read_bytes(),before)

    @unittest.skipUnless(sys.platform=='win32','Windows installer branch')
    def test_native_upgrade_restores_old_scripts_and_specializes_new_hooks(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);cache=home/'.codex/plugins/cache/xueqiu-local/xueqiu-status'
            old=cache/'old/scripts/codex_hook.py';old.parent.mkdir(parents=True);old.write_bytes(b'old hook')
            new=cache/'new'
            def fake_run(args,**kwargs):
                if args[1:3]==['plugin','add']:
                    shutil.rmtree(cache);self.fixture(new)
                    return subprocess.CompletedProcess(args,0,stdout=json.dumps({'pluginId':installer.PLUGIN,'installedPath':str(new)}))
                return subprocess.CompletedProcess(args,0)
            with patch.dict(os.environ,CODEX_HOME=str(home/'.codex'),XDG_STATE_HOME=str(home/'state')),patch.object(installer.subprocess,'run',side_effect=fake_run):
                installer.install('fake-codex',home/'market')
            self.assertEqual(old.read_bytes(),b'old hook')
            command=json.loads((new/'hooks/hooks.json').read_text())['hooks']['SessionStart'][0]['hooks'][0]['commandWindows']
            self.assertTrue(' -X utf8 ' in command or '-EncodedCommand ' in command)
            self.assertEqual(len(list((home/'state/xueqiu/plugin-upgrade-backups').glob('*/manifest.json'))),1)

    @unittest.skipUnless(sys.platform=='win32','Native Windows upgrade failure branch')
    def test_native_failure_restores_missing_files_without_overwriting_or_trust_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);cache=home/'.codex/plugins/cache/xueqiu-local/xueqiu-status'
            old=cache/'old/scripts/codex_hook.py';old.parent.mkdir(parents=True);old.write_bytes(b'old hook')
            other=cache/'old/hooks/hooks.json';other.parent.mkdir();other.write_bytes(b'original hooks')
            trust=home/'trust';trust.write_bytes(b'untouched')
            def fake_run(args,**kwargs):
                if args[1:3]==['plugin','add']:
                    shutil.rmtree(cache);old.parent.mkdir(parents=True);old.write_bytes(b'new existing file')
                    raise subprocess.CalledProcessError(23,args)
                return subprocess.CompletedProcess(args,0)
            with patch.dict(os.environ,CODEX_HOME=str(home/'.codex'),XDG_STATE_HOME=str(home/'state')),patch.object(installer.subprocess,'run',side_effect=fake_run):
                with self.assertRaises(subprocess.CalledProcessError) as failure:installer.install('fake-codex',home/'market')
            self.assertEqual(failure.exception.returncode,23)
            self.assertEqual(old.read_bytes(),b'new existing file')
            self.assertEqual(other.read_bytes(),b'original hooks');self.assertEqual(trust.read_bytes(),b'untouched')

@unittest.skipUnless(sys.platform == 'win32', 'Real native Windows shell regressions')
class NativeShellTests(unittest.TestCase):
    def launch(self, folder_name):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/folder_name
            (root/'scripts').mkdir(parents=True)
            (root/'scripts/codex_hook.py').write_text(
                'import json,sys; assert sys.argv[-1]=="hook"; '
                'value=json.load(sys.stdin); print(json.dumps({"event":value["hook_event_name"]}))',
                encoding='utf-8')
            command=windows_command(root,sys.executable)
            shells=[[os.environ['COMSPEC'],'/D','/S','/C'],
                    [shutil.which('powershell.exe'),'-NoProfile','-NonInteractive','-Command']]
            for shell in shells:
                with self.subTest(shell=Path(shell[0]).name,folder=folder_name):
                    reply=subprocess.run(shell+[command],
                        input='{"hook_event_name":"SessionStart"}',capture_output=True,
                        text=True,encoding='utf-8',errors='replace',timeout=10)
                    self.assertEqual(reply.returncode,0,reply.stderr)
                    self.assertEqual(json.loads(reply.stdout),{'event':'SessionStart'})
    def test_plain_paths_work_through_both_native_shells(self):
        self.launch('native-probe')
    def test_spaced_unicode_and_apostrophe_paths_work_through_both_shells(self):
        self.launch("native probe's 中文")

if __name__=='__main__':unittest.main()
