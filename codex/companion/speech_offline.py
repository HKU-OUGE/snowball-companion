"""Import pinned speech models from a mounted SD card into private PC storage."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote

from private_files import private_dir, checked_path


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
    if profile not in ('base','tiny','small'):return None
    manifest=json.loads(Path(__file__).with_name('speech-'+profile+'-manifest.json').read_text(encoding='utf-8'))
    cache=private_dir(cache or Path.home()/'.cache/xueqiu/speech-offline')
    target=cache/(profile+'-'+manifest['revision'])
    if all(checked_file(target/name,item) for name,item in manifest['files'].items()):return target
    for root in mounted_roots() if roots is None else roots:
        source=Path(root)/'Snowball-Setup/speech-models'/profile
        if source.is_symlink() or not source.is_dir():continue
        if not all(checked_file(source/name,item) for name,item in manifest['files'].items()):continue
        temporary=Path(tempfile.mkdtemp(prefix='.'+profile+'-',dir=cache))
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


def prepare_model(profile,roots=None,cache=None,progress=None):
    """Reuse the verified model, then resolve each pinned file by size/source."""
    from resource_fetch import ResourceFetcher
    if profile not in ('base','tiny','small'):raise ValueError('unknown model')
    manifest=json.loads(Path(__file__).with_name('speech-'+profile+'-manifest.json').read_text(encoding='utf-8'))
    cache=private_dir(cache or Path.home()/'.cache/xueqiu/speech-offline')
    target=cache/(profile+'-'+manifest['revision'])
    # Older online installations use the Hugging Face snapshot directory.
    hf=Path.home()/'.cache/xueqiu/speech-models'/('models--'+manifest['repo'].replace('/','--'))/'snapshots'/manifest['revision']
    if all(checked_file(target/name,item) for name,item in manifest['files'].items()):
        if progress:progress('resources_cache')
        return target
    fetcher=ResourceFetcher(roots=roots,cache=cache.parent/'resources',progress=progress)
    temp=Path(tempfile.mkdtemp(prefix='.'+profile+'-',dir=cache))
    try:
        for name,item in manifest['files'].items():
            source=target/name
            if not checked_file(source,item):source=hf_cached_file(hf,name,item)
            if source is None:
                url='https://huggingface.co/'+manifest['repo']+'/resolve/'+manifest['revision']+'/'+quote(name,safe='')
                source=fetcher.get(name,item,Path('Snowball-Setup/speech-models')/profile/name,url,kind='model')
            elif progress:progress('resources_cache')
            shutil.copyfile(source,temp/name)
            if not checked_file(temp/name,item):raise ValueError('resource_cache_changed')
        if target.exists():target=cache/(target.name+'-'+temp.name[6:])
        os.replace(temp,target);return target
    finally:
        if temp.exists():shutil.rmtree(temp)


def hf_cached_file(snapshot,name,entry):
    """Accept HF's normal blob links only inside this model's private repository."""
    from resource_fetch import valid
    try:
        # Check for unexpected links before resolving legitimate Windows 8.3
        # aliases. The blob link and its allowed root must use the same spelling.
        repository=checked_path(snapshot.parent.parent).resolve(strict=True)
        candidate=snapshot/name
        if candidate.is_symlink():
            candidate=candidate.resolve(strict=True)
            if not candidate.is_relative_to(repository/'blobs'):return None
        return candidate if valid(candidate,entry) else None
    except OSError:
        return None
