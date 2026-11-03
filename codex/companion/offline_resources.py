"""Copy a hash-pinned wheel set from SD to private PC cache before installation."""
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


def prepare_wheels(python,group,roots=None,cache=None):
    if group not in ('connection','speech'):raise ValueError('unknown offline group')
    key=target_key(python)
    if not key:return None
    manifest_path=Path(__file__).with_name('offline-resources.json')
    if not manifest_path.is_file():return None
    manifest=json.loads(manifest_path.read_text(encoding='utf-8')).get('platforms',{}).get(key)
    if not manifest:return None
    files=manifest['files'];digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]
    cache=private_dir(cache or Path.home()/'.cache/xueqiu/offline-wheels')
    target=cache/(key+'-'+digest)
    def verified(folder):
        return not folder.is_symlink() and all(checked_file(folder/name,entry) for name,entry in files.items())
    if verified(target):return target
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


def pip_args(python,group,packages,requirements=None):
    """No untrusted wheel executes: only bundled hash manifests can enable offline mode."""
    directory=prepare_wheels(python,group)
    if directory:
        return [str(python),'-m','pip','install','--no-index','--find-links',str(directory),
                '--require-hashes','-r',str(directory/(group+'.txt'))]
    return None


def prepare_python(roots=None,cache=None):
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
        return executable
    for root in mounted_roots() if roots is None else roots:
        archive=Path(root)/'Snowball-Setup/offline/python'/record['file']
        if not checked_file(archive,record):continue
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
