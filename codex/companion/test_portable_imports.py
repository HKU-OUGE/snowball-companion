"""Test the distributed Codex entry points, not unused historical adapter copies."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PortableEntryPoints(unittest.TestCase):
    def test_manifest_python_imports_without_posix_modules_on_windows(self):
        manifest=json.loads((ROOT.parent/'tools/plugin_bundle_files.json').read_text(encoding='utf-8'))
        scripts=[ROOT.parent/name for name in manifest['platforms']['codex'] if name.endswith('.py') and not (sys.platform!='win32' and name.endswith('/windows_file_lock.py'))]
        linux_only={'manage_hub.py'}
        code='''import builtins,runpy,sys
real_import=builtins.__import__
def guarded(name,*args,**kwargs):
    if sys.platform=='win32' and name in {'fcntl','termios','pwd','grp','pty'}:
        raise ImportError('POSIX module unavailable: '+name)
    return real_import(name,*args,**kwargs)
builtins.__import__=guarded
sys.path.insert(0,sys.argv[2]);sys.path.insert(0,sys.argv[3])
runpy.run_path(sys.argv[1],run_name='snowball_import_fixture')
'''
        with tempfile.TemporaryDirectory(prefix='Snowball 中文 ') as folder:
            env=dict(os.environ,HOME=folder,USERPROFILE=folder,CODEX_HOME=str(Path(folder)/'.codex'),PYTHONUTF8='1')
            env.pop('CODEX_APP_TOOLS_PIPE_PATH',None)
            for script in scripts:
                with self.subTest(script=str(script.relative_to(ROOT.parent))):
                    result=subprocess.run([sys.executable,'-X','utf8','-c',code,str(script),str(ROOT/'companion'),str(script.parent)],env=env,capture_output=True,timeout=15)
                    if sys.platform!='linux' and script.name in linux_only:
                        self.assertNotEqual(result.returncode,0)
                        self.assertIn(b'windows-services.ps1',result.stderr)
                    else:
                        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8','replace'))
            self.assertFalse(list(Path(folder).iterdir()),'Imports must not write recipient state')

if __name__=='__main__':unittest.main()
