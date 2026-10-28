#!/usr/bin/env python3
"""Offline installer regression tests; fake Codex, isolated HOME, no real config."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

INSTALLER = Path(__file__).resolve().parents[1] / 'install.sh'
FAKE = r'''#!/usr/bin/env python3
import os, pathlib, shutil, signal, sys
root = pathlib.Path(os.environ['CODEX_HOME'])
cache = root / 'plugins/cache/xueqiu-local/xueqiu-status'
if sys.argv[1:4] == ['plugin', 'marketplace', 'add']:
    sys.exit(0)
if sys.argv[1:4] != ['plugin', 'add', 'xueqiu-status@xueqiu-local']:
    sys.exit(55)
shutil.rmtree(cache)
new = cache / '0.2.0/scripts/codex_hook.py'
new.parent.mkdir(parents=True)
new.write_bytes(b'new version\n')
mode = os.environ.get('FAKE_MODE', '')
if mode == 'existing':
    old = cache / '0.1.0/scripts/codex_hook.py'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'created by installer\n')
if mode == 'signal':
    os.kill(os.getppid(), signal.SIGTERM)
if mode == 'failure':
    sys.exit(23)
'''


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / 'home'
        self.codex = self.home / '.codex'
        self.cache = self.codex / 'plugins/cache/xueqiu-local/xueqiu-status'
        self.originals = {'0.1.0/scripts/codex_hook.py': b'old trusted code\n',
                          '0.1.0/hooks/hooks.json': b'{"hooks": {}}\n'}
        for name, data in self.originals.items():
            path = self.cache / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            path.chmod(0o755 if name.endswith('.py') else 0o644)
        self.trust = self.codex / 'hooks-trust-fixture.json'
        self.trust.write_text('never modify trust')
        self.other = self.codex / 'plugins/cache/other-market/other-plugin/1.0/hooks/hooks.json'
        self.other.parent.mkdir(parents=True)
        self.other.write_text('untouched other plugin')
        binary = self.root / 'bin'
        binary.mkdir()
        (binary / 'codex').write_text(FAKE)
        (binary / 'codex').chmod(0o755)
        self.env = dict(os.environ, HOME=str(self.home), CODEX_HOME=str(self.codex),
                        XDG_STATE_HOME=str(self.home / '.local/state'),
                        PATH=str(binary) + os.pathsep + os.environ['PATH'])

    def run_install(self, mode=''):
        return subprocess.run(['bash', str(INSTALLER)], env=dict(self.env, FAKE_MODE=mode),
                              capture_output=True, text=True, timeout=15)

    def assert_originals(self):
        for name, data in self.originals.items():
            self.assertEqual((self.cache / name).read_bytes(), data)
        self.assertEqual((self.cache / '0.1.0/scripts/codex_hook.py').stat().st_mode & 0o777, 0o755)
        self.assertEqual(self.trust.read_text(), 'never modify trust')
        self.assertEqual(self.other.read_text(), 'untouched other plugin')
        self.assertEqual((self.cache / '0.2.0/scripts/codex_hook.py').read_bytes(), b'new version\n')

    def test_success_restores_deleted_old_hooks_and_keeps_new_version(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_originals()
        backup_root = self.home / '.local/state/xueqiu/plugin-upgrade-backups'
        self.assertEqual(backup_root.stat().st_mode & 0o777, 0o700)
        manifests = list(backup_root.glob('*/manifest.json'))
        self.assertEqual(len(manifests), 1)
        manifest = json.loads(manifests[0].read_text())
        self.assertEqual(len(manifest['files']), 2)
        for entry in manifest['files']:
            saved = manifests[0].parent / entry['path']
            self.assertEqual(hashlib.sha256(saved.read_bytes()).hexdigest(), entry['sha256'])
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)

    def test_failure_also_restores_and_preserves_cli_exit_code(self):
        result = self.run_install('failure')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assert_originals()

    def test_existing_files_are_never_overwritten(self):
        result = self.run_install('existing')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.cache / '0.1.0/scripts/codex_hook.py').read_bytes(), b'created by installer\n')
        self.assertEqual((self.cache / '0.1.0/hooks/hooks.json').read_bytes(), self.originals['0.1.0/hooks/hooks.json'])

    def test_term_signal_restores_before_exiting(self):
        result = self.run_install('signal')
        self.assertNotEqual(result.returncode, 0)
        self.assert_originals()

    def test_symlink_is_rejected_before_calling_codex(self):
        (self.cache / '0.1.0/scripts/unsafe.py').symlink_to(self.trust)
        result = self.run_install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refusing symlink', result.stderr)
        self.assertFalse((self.cache / '0.2.0').exists())
        self.assertEqual(self.trust.read_text(), 'never modify trust')


if __name__ == '__main__':
    unittest.main()
