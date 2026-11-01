"""Native installer regressions: real file/lock/ACL operations, fake Codex CLI.

Unlike test_install_plugin.py, these do not invoke bash, POSIX executable scripts,
send process signals or assert POSIX modes on Windows. All homes are fixtures.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]/'codex/tools'
sys.path.insert(0, str(TOOLS))
import install_plugin as installer
from platform_support import private_file_ok


class NativeInstallerTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077); os.umask(previous_umask)
        self.addCleanup(os.umask, previous_umask)
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)/'合成 用户'; self.home.mkdir()
        self.codex = self.home/'.codex'
        self.cache = self.codex/'plugins/cache/xueqiu-local/xueqiu-status'
        self.old = self.cache/'0.5.1/scripts/codex_hook.py'
        self.old.parent.mkdir(parents=True); self.old.write_bytes(b'old trusted fixture')
        self.trust = self.codex/'trust-fixture.json'; self.trust.write_bytes(b'unchanged trust')
        self.other = self.codex/'plugins/cache/other/plugin/hooks.json'
        self.other.parent.mkdir(parents=True); self.other.write_bytes(b'unchanged other plugin')
        self.state = self.home/'.local/state'
        env = patch.dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                         CODEX_HOME=str(self.codex), XDG_STATE_HOME=str(self.state))
        env.start(); self.addCleanup(env.stop)
        self.calls = []
        self.mode = 'success'

    def fake_cli(self, argv, **kwargs):
        self.calls.append(argv)
        self.assertTrue(kwargs.get('check'))
        self.assertEqual(argv[0], 'fixture-codex.exe')
        if argv[1:4] == ['plugin', 'marketplace', 'add']:
            return subprocess.CompletedProcess(argv, 0)
        self.assertEqual(argv[1:], ['plugin', 'add', 'xueqiu-status@xueqiu-local', '--json'])
        shutil.rmtree(self.cache)
        new = self.cache/'0.6.0/scripts/codex_hook.py'
        new.parent.mkdir(parents=True); new.write_bytes(b'new trusted fixture')
        if self.mode == 'existing':
            self.old.parent.mkdir(parents=True); self.old.write_bytes(b'replacement from CLI')
        if self.mode == 'failure':
            raise subprocess.CalledProcessError(23, argv)
        if self.mode == 'interrupted':
            raise InterruptedError('synthetic interruption')
        return subprocess.CompletedProcess(argv, 0)

    def run_install(self):
        with patch.object(installer.subprocess, 'run', side_effect=self.fake_cli):
            installer.install('fixture-codex.exe', self.home/'reviewed market')

    def assert_preserved(self):
        self.assertEqual(self.old.read_bytes(), b'old trusted fixture')
        self.assertEqual((self.cache/'0.6.0/scripts/codex_hook.py').read_bytes(), b'new trusted fixture')
        self.assertEqual(self.trust.read_bytes(), b'unchanged trust')
        self.assertEqual(self.other.read_bytes(), b'unchanged other plugin')
        manifests = list((self.state/'xueqiu/plugin-upgrade-backups').glob('*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        self.assertTrue(private_file_ok(manifests[0]))
        manifest = json.loads(manifests[0].read_text())
        for row in manifest['files']:
            self.assertTrue(private_file_ok(manifests[0].parent/row['path']))

    def test_success_preserves_old_hooks_and_private_backup(self):
        self.run_install(); self.assert_preserved(); self.assertEqual(len(self.calls), 2)

    def test_cli_failure_restores_before_propagating(self):
        self.mode = 'failure'
        with self.assertRaises(subprocess.CalledProcessError) as error: self.run_install()
        self.assertEqual(error.exception.returncode, 23)
        self.assert_preserved()

    def test_interruption_restores_before_propagating(self):
        self.mode = 'interrupted'
        with self.assertRaises(InterruptedError): self.run_install()
        self.assert_preserved()

    def test_existing_cli_created_file_is_not_overwritten(self):
        self.mode = 'existing'; self.run_install()
        self.assertEqual(self.old.read_bytes(), b'replacement from CLI')
        self.assertEqual(self.trust.read_bytes(), b'unchanged trust')

    def test_bad_backup_hash_does_not_restore(self):
        backup = self.home/'backup'; backup.mkdir()
        snapshot, records = installer.backup_hooks(self.cache, backup)
        (snapshot/records[0]['path']).write_bytes(b'tampered fixture')
        self.old.unlink()
        with self.assertRaises(RuntimeError): installer.restore_missing(self.cache, snapshot, records)
        self.assertFalse(self.old.exists())


if __name__ == '__main__': unittest.main()
