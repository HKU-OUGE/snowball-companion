"""Isolated handoff regressions. No host endpoint or real user state is touched."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import windows_app_endpoint as handoff

ONE=r'\\.\pipe\codex-app-tools-11111111-1111-4111-8111-111111111111'
TWO=r'\\.\pipe\codex-app-tools-22222222-2222-4222-8222-222222222222'
ROOT=Path(__file__).resolve().parents[1]


class Handoff(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="Snowball endpoint 中文 ")
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        self.receipt=self.home/'private/app-endpoint.json'
        self.patchers=[patch.object(handoff,'receipt_path',return_value=self.receipt),
                       patch.object(handoff.sys,'platform','win32'),
                       patch.dict(os.environ,{handoff.VARIABLE:ONE})]
        for p in self.patchers:p.start();self.addCleanup(p.stop)

    def test_background_without_environment_reads_only_private_endpoint(self):
        self.assertTrue(handoff.remember_current_endpoint())
        self.assertEqual(json.loads(self.receipt.read_text()),{'schema':1,'pipe':ONE})
        with patch.dict(os.environ,{handoff.VARIABLE:''}):
            self.assertEqual(handoff.candidates(),(ONE,))

    def test_restart_refreshes_handoff_without_rewriting_same_endpoint(self):
        handoff.remember_current_endpoint();before=self.receipt.stat().st_mtime_ns
        handoff.remember_current_endpoint();self.assertEqual(self.receipt.stat().st_mtime_ns,before)
        with patch.dict(os.environ,{handoff.VARIABLE:TWO}):handoff.remember_current_endpoint()
        self.assertEqual(handoff.candidates(),(ONE,TWO))
        with patch.dict(os.environ,{handoff.VARIABLE:''}):self.assertEqual(handoff.candidates(),(TWO,))

    def test_malformed_or_remote_value_does_not_replace_or_select_handoff(self):
        handoff.remember_current_endpoint();before=self.receipt.read_bytes()
        for value in ('tcp://localhost:1',r'\\remote\pipe\codex-app-tools-test',r'\\.\pipe\other','x'*1024):
            with self.subTest(value=value[:20]),patch.dict(os.environ,{handoff.VARIABLE:value}):
                self.assertFalse(handoff.remember_current_endpoint())
                self.assertEqual(handoff.candidates(),())
                self.assertEqual(self.receipt.read_bytes(),before)

    def test_bad_and_oversized_receipts_are_not_endpoints(self):
        self.receipt.parent.mkdir()
        for data in ('x'*513,'{}','[]','{"schema":1,"pipe":"tcp://localhost:1"}'):
            self.receipt.write_text(data)
            with patch.dict(os.environ,{handoff.VARIABLE:''}):self.assertEqual(handoff.candidates(),())

    def test_non_windows_capture_creates_no_state(self):
        with patch.object(handoff.sys,'platform','linux'):
            self.assertFalse(handoff.remember_current_endpoint())
        self.assertFalse(self.receipt.exists())

    def test_bundled_hook_and_runtime_share_handoff_logic(self):
        self.assertEqual((ROOT/'companion/windows_app_endpoint.py').read_bytes(),
                         (ROOT/'plugins/xueqiu-status/scripts/windows_app_endpoint.py').read_bytes())


@unittest.skipUnless(os.name=='nt','Native Windows hook process and DACL')
class NativeHookHandoff(unittest.TestCase):
    def test_hook_capture_survives_in_a_background_process_without_host_environment(self):
        with tempfile.TemporaryDirectory(prefix='Snowball 后台 ') as folder:
            home=Path(folder)
            env=dict(os.environ,HOME=folder,USERPROFILE=folder,CODEX_HOME=str(home/'.codex'),PYTHONUTF8='1')
            env[handoff.VARIABLE]=ONE
            hook=ROOT/'plugins/xueqiu-status/scripts/codex_hook.py'
            payload={'hook_event_name':'SessionStart','session_id':'fixture-thread'}
            result=subprocess.run([sys.executable,'-X','utf8',str(hook),'--state-dir',str(home/'state'),
                '--token-file',str(home/'missing-token'),'hook'],input=json.dumps(payload),text=True,
                encoding='utf-8',capture_output=True,env=env,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertNotIn(ONE,result.stdout+result.stderr)
            receipt=home/'.config/xueqiu/codex-app-endpoint.json'
            self.assertTrue(receipt.is_file())
            env.pop(handoff.VARIABLE)
            code='from windows_app_endpoint import candidates;import json;print(json.dumps(candidates()))'
            result=subprocess.run([sys.executable,'-X','utf8','-c',code],cwd=ROOT/'companion',
                capture_output=True,text=True,encoding='utf-8',env=env,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout),[ONE])
            dump=home/'acl.txt'
            subprocess.run(['icacls',str(receipt),'/save',str(dump)],check=True,capture_output=True)
            sddl=dump.read_text(encoding='utf-16-le').lstrip('\ufeff')
            self.assertIn('D:P',sddl);self.assertIn(';;;SY)',sddl)
            self.assertEqual(sddl.count('(A;'),2);self.assertNotIn(';;;WD)',sddl)


if __name__=='__main__':unittest.main()
