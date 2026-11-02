"""Native Windows fixtures only; never alter a real user's services or credentials."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock

from private_files import private_write, private_file, safe_reader


@unittest.skipUnless(os.name == 'nt','Native Windows security and entry points')
class PrivateFilesTests(unittest.TestCase):
    def test_unicode_atomic_write_and_private_dacl_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'中文 folder'/'状态.json'
            private_write(path,'第一次'.encode())
            private_write(path,'第二次'.encode())
            with safe_reader(path) as stream:self.assertEqual(stream.read(),'第二次'.encode())
            subprocess.run(['icacls',str(path),'/grant','*S-1-1-0:R'],check=True,capture_output=True)
            private_file(path)
            dump=Path(folder)/'acl.txt'
            subprocess.run(['icacls',str(path),'/save',str(dump)],check=True,capture_output=True)
            sddl=dump.read_text(encoding='utf-16-le').lstrip('\ufeff')
            self.assertNotIn(';;;WD)',sddl)
            self.assertIn(';;;SY)',sddl)
            self.assertEqual(sddl.count('(A;'),2)
            self.assertIn('D:P',sddl)
            self.assertFalse(list(path.parent.glob('.private-*')))

    def test_junction_parent_rejected_without_modifying_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'target';target.mkdir();file=target/'fixture';file.write_bytes(b'original')
            link=root/'junction'
            subprocess.run(['cmd.exe','/d','/c','mklink','/J',str(link),str(target)],check=True,capture_output=True)
            try:
                with self.assertRaises(OSError):
                    with safe_reader(link/'fixture'):pass
                with self.assertRaises(OSError):private_write(link/'fixture',b'replaced')
                self.assertEqual(file.read_bytes(),b'original')
            finally:os.rmdir(link)

    def test_missing_pipe_never_attempts_unix_fallback(self):
        from codex_desktop import CodexDesktop,DeliveryError
        desktop=CodexDesktop();desktop.instruction_route=Mock(return_value={'thread':'fixture'})
        desktop.app_tools=Mock();desktop.app_tools.available.return_value=False
        desktop.ipc_factory=Mock(side_effect=AssertionError('Unix IPC invoked'))
        with self.assertRaises(DeliveryError) as failure:desktop.deliver_instruction('s','fixture','r')
        self.assertFalse(failure.exception.uncertain)
        desktop.ipc_factory.assert_not_called()

    def test_linux_managers_refuse_before_work_and_without_tracebacks(self):
        root=Path(__file__).parent
        with tempfile.TemporaryDirectory() as folder:
            env=dict(os.environ,USERPROFILE=folder,HOME=folder)
            for name,args in [('manage.py',['start']),('manage_hub.py',['start']),('user_services.py',['install'])]:
                with self.subTest(name=name):
                    result=subprocess.run([sys.executable,str(root/name),*args],env=env,capture_output=True,text=True,timeout=5)
                    self.assertNotEqual(result.returncode,0)
                    self.assertIn('windows-services.ps1',result.stderr)
                    self.assertNotIn('Traceback',result.stderr)
            self.assertFalse(list(Path(folder).iterdir()))

    def test_bundled_hook_and_runtime_security_helpers_match(self):
        runtime=Path(__file__).with_name('private_files.py')
        bundled=Path(__file__).parents[1]/'plugins/xueqiu-status/scripts/private_files.py'
        self.assertEqual(runtime.read_bytes(),bundled.read_bytes())


if __name__=='__main__':unittest.main()
