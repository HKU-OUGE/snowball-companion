"""Build native installer assets from an explicit public file allowlist.

Run on the target OS/architecture. Python downloads are pinned by SHA-256;
the recipient needs neither Python, Node.js, Git nor an agent to open the wizard.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tarfile
import urllib.parse
import urllib.request

from build_plugin_bundles import ROOT, RELEASE, read_source, source_lists

INSTALLER_FILES = ('installer/bootstrap.py', 'installer/shortcut.ps1', 'installer/runtimes.json', 'installer/README.md')


def platform_id():
    arch = 'arm64' if platform.machine().lower() in ('aarch64', 'arm64') else 'x64'
    return ('windows' if os.name == 'nt' else 'macos' if sys.platform == 'darwin' else 'linux') + '-' + arch


def build(output, cache):
    target = platform_id()
    lock = json.loads((ROOT / 'installer/runtimes.json').read_text())
    if target not in lock:
        raise RuntimeError('No reviewed runtime for this target')
    output = output.resolve();cache = cache.resolve()
    output.mkdir(parents=True, exist_ok=True);cache.mkdir(parents=True, exist_ok=True)
    payload = output / ('payload-' + target)
    if payload.exists():
        raise RuntimeError('Use an empty output directory; existing builds are not overwritten')
    payload.mkdir()
    spec = lock[target]
    archive = cache / spec['asset']
    if not archive.exists():
        url = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261026/' + urllib.parse.quote(spec['asset'])
        temporary = archive.with_suffix('.partial')
        urllib.request.urlretrieve(url, temporary)
        temporary.rename(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != spec['sha256']:
        raise RuntimeError('Python runtime checksum mismatch')
    with tarfile.open(archive) as tar:
        tar.extractall(payload, filter='data')
    python = payload / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3')
    manifest = source_lists(ROOT)
    files = sorted(set(manifest['common'] + sum(manifest['platforms'].values(), []) + list(INSTALLER_FILES)))
    hashes = {}
    for name in files:
        data = read_source(ROOT, name)
        path = payload / name;path.parent.mkdir(parents=True, exist_ok=True);path.write_bytes(data)
        path.chmod(0o755 if path.suffix == '.sh' else 0o644)
        hashes[name] = hashlib.sha256(data).hexdigest()
    subprocess.run([str(python), '-m', 'pip', 'install', '--only-binary=:all:', '--disable-pip-version-check',
                    'bleak==3.0.2', 'pyserial==3.5', 'cryptography==50.0.2'], check=True)
    packages = subprocess.check_output([str(python), '-m', 'pip', 'freeze'], text=True)
    info = {'product':'snowball-companion','version':RELEASE,'platform':target,
            'python_archive':spec['asset'],'python_sha256':spec['sha256'],
            'dependencies':packages.splitlines(),'application_sha256':hashes,
            'credentials_included':False,'firmware_included':False}
    (payload / 'INSTALLER.json').write_text(json.dumps(info, indent=2) + '\n')
    subprocess.run([str(python), '-X', 'utf8', str(payload / 'installer/bootstrap.py'), '--self-test'], check=True)
    stem = 'Snowball-Setup-' + RELEASE + '-' + target
    if os.name == 'nt':
        nsis = shutil.which('makensis') or str(Path(os.environ.get('ProgramFiles(x86)', 'C:/Program Files (x86)')) / 'NSIS/makensis.exe')
        asset = output / (stem + '.exe')
        subprocess.run([nsis, '/DPAYLOAD=' + str(payload), '/DOUTPUT=' + str(asset), str(ROOT / 'installer/windows.nsi')], check=True)
    elif sys.platform == 'darwin':
        volume = output / 'volume';volume.mkdir()
        app = volume / 'Install Snowball.app';resources = app / 'Contents/Resources';resources.mkdir(parents=True)
        shutil.move(str(payload), str(resources / 'payload'))
        payload = resources / 'payload'
        executable = app / 'Contents/MacOS/Snowball';executable.parent.mkdir(parents=True)
        executable.write_text('#!/bin/sh\nset -eu\nbase="$(CDPATH= cd -- "$(dirname -- "$0")/../Resources/payload" && pwd)"\nexec "$base/python/bin/python3" -X utf8 "$base/installer/bootstrap.py"\n')
        executable.chmod(0o755)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'pet.snowball.installer',
            'CFBundleName':'Install Snowball','CFBundleExecutable':'Snowball','CFBundlePackageType':'APPL',
            'CFBundleVersion':RELEASE,'CFBundleShortVersionString':RELEASE,'LSMinimumSystemVersion':'12.0',
            'NSBluetoothAlwaysUsageDescription':'Snowball connects to your companion device to show AI session status.'}))
        shutil.copyfile(ROOT / 'installer/README.md', volume / 'READ-ME.txt')
        asset = output / (stem + '.dmg')
        subprocess.run(['hdiutil', 'create', '-volname', 'Snowball Companion', '-srcfolder', str(volume),
                        '-ov', '-format', 'UDZO', str(asset)], check=True)
    else:
        # Portable tarball retains executable modes; no FUSE or root installation.
        start = payload / 'Install-Snowball.sh'
        start.write_text('#!/bin/sh\nset -eu\nbase="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"\nexec "$base/python/bin/python3" -X utf8 "$base/installer/bootstrap.py" "$@"\n')
        start.chmod(0o755)
        asset = output / (stem + '.tar.gz')
        with tarfile.open(asset, 'w:gz') as tar:
            tar.add(payload, arcname='Snowball-Setup', recursive=True)
    digest = hashlib.sha256(asset.read_bytes()).hexdigest()
    (output / (asset.name + '.sha256')).write_text(digest + '  ' + asset.name + '\n')
    (output / ('build-' + target + '.json')).write_text(json.dumps({'version':RELEASE,'platform':target,
        'asset':asset.name,'bytes':asset.stat().st_size,'sha256':digest,'python_smoke_passed':True,
        'application_file_count':len(hashes)}, indent=2) + '\n')
    print('INSTALLER_READY', asset)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, required=True);p.add_argument('--cache', type=Path, required=True)
    args = p.parse_args();build(args.output,args.cache)
