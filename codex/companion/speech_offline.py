"""Import the pinned Base model from a mounted Snowball SD card, never execute from it."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from private_files import private_dir


def mounted_roots():
    if os.name == 'nt':
        mask=ctypes.windll.kernel32.GetLogicalDrives()
        # Include removable and USB fixed drives: some card readers report fixed.
        return [Path(chr(65+i)+':/') for i in range(26) if mask & (1<<i) and
                ctypes.windll.kernel32.GetDriveTypeW(chr(65+i)+':\\') in (2,3)]
    folders=[Path('/media')/Path.home().name,Path('/run/media')/Path.home().name,Path('/Volumes')]
    roots=[]
    for folder in folders:
        try:roots.extend(p for p in folder.iterdir() if p.is_dir())
        except OSError:pass
    return roots[:64]


def checked_file(path,entry):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != entry['bytes']:return False
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()==entry['sha256']


def import_available(profile,roots=None,cache=None):
    if profile!='base':return None
    manifest=json.loads(Path(__file__).with_name('speech-base-manifest.json').read_text(encoding='utf-8'))
    cache=private_dir(cache or Path.home()/'.cache/xueqiu/speech-offline')
    target=cache/(profile+'-'+manifest['revision'])
    if all(checked_file(target/name,item) for name,item in manifest['files'].items()):return target
    for root in mounted_roots() if roots is None else roots:
        source=Path(root)/'Snowball-Setup/speech-models/base'
        if source.is_symlink() or not source.is_dir():continue
        if not all(checked_file(source/name,item) for name,item in manifest['files'].items()):continue
        temporary=Path(tempfile.mkdtemp(prefix='.base-',dir=cache))
        try:
            for name,item in manifest['files'].items():
                shutil.copyfile(source/name,temporary/name)
                if not checked_file(temporary/name,item):raise ValueError('speech_sd_changed')
            # Existing invalid cache is left in place; never broad-delete user data.
            if target.exists():target=cache/(profile+'-'+manifest['revision']+'-'+temporary.name[6:])
            os.replace(temporary,target)
            return target
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    return None
