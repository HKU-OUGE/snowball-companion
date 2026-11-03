"""Prepare pinned platform resources; small SD files and large network downloads."""
import hashlib,json,os,platform,shutil,subprocess,sys,tempfile
from pathlib import Path
from private_files import private_dir
from speech_offline import mounted_roots,checked_file


def target_key(python):
    code="import sys,platform,json;print(json.dumps([sys.platform,platform.machine().lower(),sys.version_info.major,sys.version_info.minor,platform.mac_ver()[0]]))"
    kwargs={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
    p=subprocess.run([str(python),'-X','utf8','-c',code],check=True,capture_output=True,text=True,timeout=10,**kwargs)
    system,machine,major,minor,mac_version=json.loads(p.stdout)
    if major!=3 or minor not in (12,13):return None
    cpu='x64' if machine in ('amd64','x86_64') else 'arm64' if machine in ('aarch64','arm64') else None
    system={'win32':'windows','linux':'linux','darwin':'macos'}.get(system)
    if not cpu or not system or (cpu=='arm64' and system!='macos'):return None
    if system=='macos':
        try:major_os=int(mac_version.split('.')[0])
        except (AttributeError,ValueError):return None
        if major_os<(14 if cpu=='arm64' else 15):return None
    return f'{system}-{cpu}-py{major}{minor}'


def prepare_wheels(python,group,roots=None,cache=None,progress=None):
    if group not in ('connection','speech'):raise ValueError('unknown offline group')
    key=target_key(python)
    if not key:return None
    manifest_path=Path(__file__).with_name('offline-resources.json')
    if not manifest_path.is_file():return None
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')).get('platforms',{}).get(key)
    if not manifest:return None
    all_files=manifest['files']
    digest=hashlib.sha256(json.dumps({'files':all_files},sort_keys=True).encode()).hexdigest()[:16]
    selected=manifest.get('groups',{}).get(group)
    files={n:all_files[n] for n in [*selected['files'],group+'.txt']} if selected else all_files
    cache=private_dir(cache or Path.home()/'.cache/xueqiu/offline-wheels')
    legacy=cache/(key+'-'+digest)
    target=cache/(key+'-'+digest+'-'+group) if selected else legacy
    def verified(folder):
        return not folder.is_symlink() and all(checked_file(folder/name,entry) for name,entry in files.items())
    for existing in dict.fromkeys((target,legacy)):
        if verified(existing):
            if progress:progress('resources_cache')
            return existing
    if selected:
        from resource_fetch import ResourceFetcher,wheel_url
        fetcher=ResourceFetcher(roots=roots,cache=cache.parent/'resources',progress=progress)
        temp=Path(tempfile.mkdtemp(prefix='.import-',dir=cache))
        try:
            requirements=temp/(group+'.txt')
            requirements.write_bytes(selected['requirements'].encode('utf-8'))
            if not checked_file(requirements,files[group+'.txt']):raise ValueError('bundled_requirements_invalid')
            for name in selected['files']:
                item=files[name]
                source=fetcher.get(name,item,Path('Snowball-Setup/offline/wheels')/key/name,
                                   lambda n=name,e=item:wheel_url(n,e))
                shutil.copyfile(source,temp/name)
                if not checked_file(temp/name,item):raise ValueError('resource_cache_changed')
            if target.exists():target=cache/(target.name+'-'+temp.name[8:])
            os.replace(temp,target);return target
        finally:
            if temp.exists():shutil.rmtree(temp)
    for root in mounted_roots() if roots is None else roots:
        source=Path(root)/'Snowball-Setup/offline/wheels'/key
        if not source.is_dir() or not verified(source):continue
        temp=Path(tempfile.mkdtemp(prefix='.import-',dir=cache))
        try:
            for name,item in files.items():
                shutil.copyfile(source/name,temp/name)
                if not checked_file(temp/name,item):raise ValueError('offline_resource_changed')
            if target.exists():target=cache/(key+'-'+digest+'-'+temp.name[8:])
            os.replace(temp,target);return target
        finally:
            if temp.exists():shutil.rmtree(temp)
    return None


def pip_args(python,group,packages,requirements=None,progress=None):
    """No untrusted wheel executes: only bundled hash manifests can enable offline mode."""
    directory=prepare_wheels(python,group,progress=progress)
    if directory:
        return [str(python),'-m','pip','install','--no-index','--find-links',str(directory),
                '--require-hashes','-r',str(directory/(group+'.txt'))]
    return None


def prepare_python(roots=None,cache=None,progress=None):
    """Optional agent fallback for a missing/incomplete system Python installation."""
    import tarfile
    machine=platform.machine().lower()
    cpu='x64' if machine in ('amd64','x86_64') else 'arm64' if machine in ('aarch64','arm64') else None
    system={'win32':'windows','linux':'linux','darwin':'macos'}.get(sys.platform)
    record=json.loads(Path(__file__).with_name('offline-resources.json').read_text(encoding='utf-8')).get('python',{}).get(f'{system}-{cpu}')
    if not record:return None
    cache=private_dir(cache or Path.home()/'.local/share/xueqiu/offline-python')
    folder=cache/(f'{system}-{cpu}-'+record['version'])
    relative='python/python.exe' if os.name=='nt' else 'python/bin/python3'
    executable=folder/relative
    if executable.is_file() and (folder/'SOURCE.sha256').is_file() and (folder/'SOURCE.sha256').read_text().strip()==record['sha256']:
        if progress:progress('resources_cache')
        return executable
    from resource_fetch import ResourceFetcher
    archive=ResourceFetcher(roots=roots,cache=cache.parent/'resources',progress=progress).get(record['file'],record,
        Path('Snowball-Setup/offline/python')/record['file'],record['url'],kind='runtime')
    if archive:
        temporary=Path(tempfile.mkdtemp(prefix='.python-',dir=cache))
        try:
            with tarfile.open(archive,'r:gz') as tar:
                # The archive is pinned to an upstream release digest, and the
                # data filter adds traversal/link checks during extraction.
                tar.extractall(temporary,filter='data')
            candidate=temporary/relative
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
            subprocess.run([str(candidate),'-I','-c','import venv,ensurepip,ssl'],check=True,timeout=20,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
            (temporary/'SOURCE.sha256').write_text(record['sha256']+'\n')
            if folder.exists():folder=cache/(folder.name+'-'+temporary.name[8:])
            os.replace(temporary,folder);return folder/relative
        finally:
            if temporary.exists():shutil.rmtree(temporary)
    return None


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-python',action='store_true',required=True)
    parser.parse_args()
    result=prepare_python()
    if not result:raise SystemExit('Matching verified Python runtime was not found on the mounted SD card.')
    print(result)
