"""OS-native lock/privacy tests; fixtures never use the real token or services."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import platform_support as p


class PlatformSupportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'state.lock'

    def child(self, blocking=False):
        script = '''import sys,os
sys.path.insert(0,sys.argv[1])
import platform_support as p
fd=p.open_nofollow(sys.argv[2],os.O_RDWR)
print('ready',flush=True)
try:
 p.flock(fd,p.LOCK_EX if sys.argv[3]=='block' else p.LOCK_EX|p.LOCK_NB)
except BlockingIOError:
 print('busy',flush=True)
else:
 print('acquired',flush=True)
 p.flock(fd,p.LOCK_UN)
finally: os.close(fd)
'''
        return subprocess.Popen([sys.executable, '-c', script, str(Path(p.__file__).parent),
                                 str(self.path), 'block' if blocking else 'nonblock'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def test_exclusive_process_lock_does_not_change_json_or_empty_file(self):
        for original in (b'', b'{"saved":true}\n'):
            self.path.write_bytes(original)
            with os.fdopen(p.open_nofollow(self.path, os.O_RDWR), 'r+b') as stream:
                p.flock(stream, p.LOCK_EX)
                child = self.child()
                out, err = child.communicate(timeout=10)
                self.assertEqual((child.returncode, out, err), (0, 'ready\nbusy\n', ''))
                p.flock(stream, p.LOCK_UN)
            self.assertEqual(self.path.read_bytes(), original)
            child = self.child()
            out, err = child.communicate(timeout=10)
            self.assertEqual((child.returncode, out, err), (0, 'ready\nacquired\n', ''))

    def test_blocking_lock_acquires_after_close(self):
        fd = p.open_nofollow(self.path, os.O_CREAT | os.O_RDWR)
        try:
            p.flock(fd, p.LOCK_EX)
            child = self.child(blocking=True)
            self.assertEqual(child.stdout.readline(), 'ready\n')
            self.assertIsNone(child.poll())
        finally:
            os.close(fd)
        out, err = child.communicate(timeout=10)
        self.assertEqual((child.returncode, out, err), (0, 'acquired\n', ''))

    def test_invalid_lock_and_open_failure_do_not_mutate(self):
        self.path.write_bytes(b'unchanged')
        with self.assertRaises(ValueError): p.flock(0, 999)
        with self.assertRaises((OSError, ValueError)): p.flock(-1, p.LOCK_EX | p.LOCK_NB)
        with self.assertRaises(FileExistsError):
            p.open_nofollow(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        self.assertEqual(self.path.read_bytes(), b'unchanged')
        with self.assertRaises(FileNotFoundError): p.open_nofollow(self.root/'absent', os.O_RDONLY)

    def test_private_file_and_directory(self):
        p.ensure_private_dir(self.root/'private')
        self.path.write_bytes(b'fixture')
        p.protect_private(self.path)
        self.assertTrue(p.private_file_ok(self.path))
        self.assertFalse(p.private_file_ok(self.root))
        self.assertFalse(p.private_file_ok(self.root/'absent'))
        if os.name != 'nt':
            self.path.chmod(0o644)
            self.assertFalse(p.private_file_ok(self.path))
            self.assertEqual((self.root/'private').stat().st_mode & 0o777, 0o700)

    def test_hardlink_secret_is_rejected(self):
        self.path.write_bytes(b'fixture')
        os.link(self.path, self.root/'alias')
        self.assertFalse(p.private_file_ok(self.path))
        with self.assertRaises(OSError): p.protect_private(self.path)

    def test_symlink_is_never_truncated(self):
        target = self.root/'target'
        target.write_bytes(b'unchanged')
        try:
            self.path.symlink_to(target)
        except OSError as e:
            if os.name == 'nt' and getattr(e, 'winerror', None) == 1314:
                self.skipTest('Windows runner has no symlink privilege')
            raise
        self.assertFalse(p.private_file_ok(self.path))
        with self.assertRaises(OSError): p.open_nofollow(self.path, os.O_WRONLY | os.O_TRUNC)
        self.assertEqual(target.read_bytes(), b'unchanged')

    def test_three_standalone_copies_match(self):
        root = Path(__file__).resolve().parents[1]
        blobs = [(root/relative).read_bytes() for relative in (
            'tools/platform_support.py', 'companion/platform_support.py',
            'plugins/xueqiu-status/scripts/platform_support.py')]
        self.assertEqual(blobs[0], blobs[1]); self.assertEqual(blobs[1], blobs[2])

    def test_core_entrypoints_and_exported_hook_start(self):
        root = Path(__file__).resolve().parents[1]
        for relative in ('companion/status_hub.py', 'tools/install_plugin.py',
                         'plugins/xueqiu-status/scripts/codex_hook.py',
                         'plugins/xueqiu-status/scripts/pet_setup.py'):
            result = subprocess.run([sys.executable, str(root/relative), '--help'],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
        destination = self.root/'export with spaces'
        result = subprocess.run([sys.executable,
            str(root/'plugins/xueqiu-status/scripts/codex_hook.py'),
            'export-plugin', str(destination)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((destination/'scripts/platform_support.py').read_bytes(),
                         (root/'plugins/xueqiu-status/scripts/platform_support.py').read_bytes())
        result = subprocess.run([sys.executable, str(destination/'scripts/codex_hook.py'), '--help'],
                                cwd=self.root, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_command_line_windows_uses_process_quoting(self):
        argv = ['C:\\Program Files\\Python\\python.exe', 'a b.py', 'x"y']
        with patch.object(p, '_WINDOWS', True):
            self.assertEqual(p.command_line(argv), subprocess.list2cmdline(argv))

    @unittest.skipUnless(os.name == 'nt', 'Real Windows DACL required')
    def test_windows_broad_acl_rejected_and_protection_repairs(self):
        self.path.write_bytes(b'fixture')
        descriptor = p.ctypes.c_void_p()
        self.assertTrue(p._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'D:P(A;;FA;;;WD)', 1, p.ctypes.byref(descriptor), None))
        try:
            self.assertTrue(p._advapi.SetFileSecurityW(str(self.path), 0x80000004, descriptor))
        finally:
            p._kernel.LocalFree(descriptor)
        self.assertFalse(p.private_file_ok(self.path))
        p.protect_private(self.path)
        self.assertTrue(p.private_file_ok(self.path))

    @unittest.skipUnless(os.name == 'nt', 'Real Windows DACL required')
    def test_windows_null_acl_is_rejected(self):
        self.path.write_bytes(b'fixture')
        descriptor = p.ctypes.c_void_p()
        self.assertTrue(p._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            'D:NO_ACCESS_CONTROL', 1, p.ctypes.byref(descriptor), None))
        try:
            self.assertTrue(p._advapi.SetFileSecurityW(str(self.path), 0x80000004, descriptor))
        finally:
            p._kernel.LocalFree(descriptor)
        self.assertFalse(p.private_file_ok(self.path))
        p.protect_private(self.path)
        self.assertTrue(p.private_file_ok(self.path))

    @unittest.skipUnless(os.name == 'nt', 'Real Windows DACL required')
    def test_windows_acl_query_failure_is_closed(self):
        self.path.write_bytes(b'fixture')
        with patch.object(p, '_security_info', side_effect=OSError('denied')):
            self.assertFalse(p.private_file_ok(self.path))
            with self.assertRaises(OSError): p.protect_private(self.path)

    @unittest.skipUnless(os.name == 'nt', 'Real Windows junction required')
    def test_windows_junction_ancestor_refused(self):
        target = self.root/'real'; target.mkdir()
        target.joinpath('token').write_text('fixture')
        junction = self.root/'junction'
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(junction), str(target)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertFalse(p.private_file_ok(junction/'token'))
            with self.assertRaises(OSError): p.open_nofollow(junction/'token', os.O_RDONLY)
        finally:
            junction.rmdir()


if __name__ == '__main__': unittest.main()
