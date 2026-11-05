"""Copy this verified application to a stable per-user location and open its wizard.

This bootstrap has no package manager / sudo / shell evaluation and never installs
an adapter or service until the user chooses features in the authenticated page.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VERSION = '0.7.0'


def home_root():
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Snowball Companion'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Snowball Companion'
    return Path.home() / '.local/share/snowball-companion/installer'


def python_at(root):
    return root / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3')


def verify(root):
    info = json.loads((root / 'INSTALLER.json').read_text(encoding='utf-8'))
    if info.get('version') != VERSION or info.get('product') != 'snowball-companion':
        raise RuntimeError('This is not the expected Snowball installer.')
    for name, digest in info['application_sha256'].items():
        p = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or p.is_symlink():
            raise RuntimeError('Invalid application manifest.')
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Application verification failed. Download the installer again.')
    if not python_at(root).is_file():
        raise RuntimeError('The bundled Python environment is missing.')
    return info


def stage(source, base=None):
    source = Path(source).resolve()
    info = verify(source)
    base = Path(base or home_root())
    # Refuse path redirection, but do not replace or erase any other installation.
    for p in [base, *base.parents]:
        if p.is_symlink():
            raise RuntimeError('The installation directory must not be a symlink.')
    base.mkdir(parents=True, exist_ok=True)
    target = base / VERSION
    if target.exists():
        current = verify(target)
        if current['application_sha256'] != info['application_sha256']:
            raise RuntimeError('A different build already uses this version; it has been preserved.')
        return target
    temporary = Path(tempfile.mkdtemp(prefix='.install-', dir=base))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=True)
        verify(temporary)
        temporary.rename(target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return target


def shortcut(root):
    python = python_at(root)
    launcher = root / 'codex/companion/console_launcher.py'
    if os.name == 'nt':
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                        '-File', str(root / 'installer/shortcut.ps1'), '-Python', str(python),
                        '-Launcher', str(launcher)], check=True, timeout=30)
    elif sys.platform == 'darwin':
        import plistlib
        import shlex
        app = Path.home() / 'Applications/Snowball Console.app'
        if app.exists() and not (app / 'Contents/snowball-owned').is_file():
            raise RuntimeError('An unrelated Snowball Console application already exists.')
        folder = app / 'Contents/MacOS';folder.mkdir(parents=True, exist_ok=True)
        start = folder / 'Snowball'
        start.write_text('#!/bin/sh\nexec ' + shlex.join([str(python), '-X', 'utf8', str(launcher), '--installer']) + '\n')
        start.chmod(0o755)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleName':'Snowball Console',
            'CFBundleIdentifier':'pet.snowball.console.launcher','CFBundleVersion':VERSION,
            'CFBundleExecutable':'Snowball','CFBundlePackageType':'APPL'}))
        (app / 'Contents/snowball-owned').write_text('snowball-companion\n')
    else:
        apps = Path.home() / '.local/share/applications';apps.mkdir(parents=True, exist_ok=True)
        target = apps / 'snowball-console.desktop'
        if target.exists() and 'X-Snowball-Owned=true' not in target.read_text():
            raise RuntimeError('An unrelated Snowball desktop entry already exists.')
        def quoted(p):
            return '"' + str(p).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
        target.write_text('[Desktop Entry]\nType=Application\nName=Snowball Console\nComment=Configure Snowball Companion\n'
            + 'Exec=' + quoted(python) + ' -X utf8 ' + quoted(launcher) + ' --installer\n'
            + 'Icon=' + str(root / 'codex/companion/console_assets/snowball.png') + '\nTerminal=false\nCategories=Utility;\nX-Snowball-Owned=true\n')
        target.chmod(0o755)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--destination', type=Path, help='Isolated smoke-test destination; optional')
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    if args.self_test:
        verify(source)
        sys.path.insert(0, str(source / 'codex/companion'))
        import bleak, serial, cryptography, console_server, console_installer
        print(json.dumps({'ok':True,'version':VERSION,'platform':sys.platform,'bundled_python':sys.version.split()[0]}))
        return
    root = stage(source, args.destination)
    shortcut(root)
    launch = [str(python_at(root)), '-X', 'utf8', str(root / 'codex/companion/console_launcher.py'), '--installer']
    if args.no_browser:
        launch.append('--no-browser')
    subprocess.run(launch, check=True)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        message = 'Snowball setup could not finish: ' + str(error)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, 'Snowball Companion', 0x10)
        else:
            print(message, file=sys.stderr)
        raise SystemExit(1)
