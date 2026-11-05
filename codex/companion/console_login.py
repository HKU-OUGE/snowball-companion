"""Inspect and enable current-user Snowball login services without adopting foreign units."""
import json
import os
import plistlib
import shlex
from pathlib import Path
import subprocess
import sys

from console_backend import SetupError, child_options, interpreter
from private_files import checked_path

MAC_LABEL = 'pet.snowball.console'


def mac_app(root):
    """An identifiable app owns Bluetooth privacy prompts, including at login."""
    app = Path.home() / 'Applications/Snowball Console.app'
    marker = app / 'Contents/snowball-owned'
    if app.is_symlink() or (app.exists() and (not marker.is_file() or marker.read_text().strip() != 'snowball-companion')):
        raise SetupError('autostart_conflict')
    folder = app / 'Contents/MacOS';folder.mkdir(parents=True, exist_ok=True)
    start = folder / 'Snowball'
    prefix = [interpreter(root), '-X', 'utf8']
    start.write_text('#!/bin/sh\nif [ "${1-}" = "--background" ]; then\n  exec ' +
        shlex.join(prefix + [str(root / 'console_server.py')]) + '\nfi\nexec ' +
        shlex.join(prefix + [str(root / 'console_launcher.py'), '--installer']) + '\n')
    start.chmod(0o755)
    info = {'CFBundleName':'Snowball Console','CFBundleIdentifier':'pet.snowball.console.launcher',
            'CFBundleVersion':'0.7.0','CFBundleExecutable':'Snowball','CFBundlePackageType':'APPL',
            'LSUIElement':True,
            'NSBluetoothAlwaysUsageDescription':'Snowball uses Bluetooth to sync your AI session status with your companion device.',
            'NSLocalNetworkUsageDescription':'Snowball syncs with your companion device on your local network.'}
    (app / 'Contents/Info.plist').write_bytes(plistlib.dumps(info))
    marker.write_text('snowball-companion\n')
    return app


def mac_arguments():
    return ['/usr/bin/open', '-g', '-a', str(Path.home() / 'Applications/Snowball Console.app'), '--args', '--background']


def mac_status(root):
    path = Path.home() / 'Library/LaunchAgents' / (MAC_LABEL + '.plist')
    if not path.exists():
        return {'state': 'absent'}
    try:
        checked_path(path)
        value = plistlib.loads(path.read_bytes())
        args = value.get('ProgramArguments', [])
        if (value.get('Label') != MAC_LABEL or value.get('SnowballOwner') != str(root)
                or args != mac_arguments()):
            return {'state': 'conflict'}
        result = subprocess.run(['launchctl', 'print', f'gui/{os.getuid()}/{MAC_LABEL}'],
                                capture_output=True, timeout=8)
        return {'state': 'ready' if result.returncode == 0 else 'inactive'}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {'state': 'conflict'}


def mac_enable(root):
    from private_files import private_dir, private_write
    path = Path.home() / 'Library/LaunchAgents' / (MAC_LABEL + '.plist')
    private_dir(path.parent)
    logs = private_dir(Path.home() / '.local/state/xueqiu/console')
    mac_app(root)
    value = {'Label': MAC_LABEL, 'SnowballOwner': str(root),
             'ProgramArguments': mac_arguments(),
             'RunAtLoad': True, 'WorkingDirectory': str(root), 'Umask': 0o077,
             'StandardOutPath': str(logs / 'login.log'), 'StandardErrorPath': str(logs / 'login.log')}
    private_write(path, plistlib.dumps(value))
    subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(path)],
                   check=True, capture_output=True, timeout=20)


def status(root):
    if os.name == 'nt':
        try:
            p = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                str(root / 'windows-login-info.ps1')], capture_output=True,
                               timeout=15, **child_options())
            value = json.loads(p.stdout.decode('utf-8-sig')) if p.returncode == 0 else {}
            return value if value.get('state') in ('ready', 'absent', 'conflict', 'inactive') else {'state': 'conflict'}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return {'state': 'conflict'}
    if sys.platform == 'darwin':
        return mac_status(root)
    if sys.platform != 'linux':
        return {'state': 'unsupported'}
    folder = Path.home() / '.config/systemd/user'
    present = []
    for name, script in (('hub', 'status_hub.py'), ('bridge', 'bridge.py')):
        path = folder / ('snowball-' + name + '.service')
        if not path.exists():
            present.append(False)
            continue
        try:
            checked_path(path)
            line = path.read_text().splitlines()[0]
            prefix = '# Snowball managed install: '
            target = Path(line[len(prefix):]) if line.startswith(prefix) else Path()
            checked_path(target)
            if not target.is_file() or target.name != script or target.stat().st_uid != os.getuid():
                return {'state': 'conflict'}
            result = subprocess.run(['systemctl', '--user', 'is-enabled', '--quiet', path.name], timeout=8, capture_output=True)
            present.append(result.returncode == 0)
        except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
            return {'state': 'conflict'}
    if all(present):
        return {'state': 'ready'}
    if any((folder / ('snowball-' + n + '.service')).exists() for n in ('hub', 'bridge')):
        # Existing services may belong to a newer shared installation. Do not replace them.
        return {'state': 'conflict'}
    return {'state': 'absent'}


def enable(root):
    current = status(root)['state']
    if current == 'ready':
        return
    if current == 'unsupported':
        raise SetupError('autostart_unsupported')
    if current in ('conflict', 'inactive'):
        raise SetupError('autostart_conflict')
    if sys.platform == 'darwin':
        mac_enable(root)
        if status(root)['state'] != 'ready':
            raise SetupError('autostart_failed')
        return
    if os.name == 'nt':
        python = Path(interpreter(root)); pythonw = python.with_name('pythonw.exe')
        args = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                str(root / 'windows-services.ps1'), '-Action', 'Install', '-Python', str(pythonw if pythonw.exists() else python)]
    else:
        args = [interpreter(root), '-X', 'utf8', str(root / 'user_services.py'), 'install', '--python', interpreter(root)]
    result = subprocess.run(args, capture_output=True, timeout=90, **child_options())
    # A not-yet-paired bridge may still exit after valid registration.
    # Login readiness means verified registration; connection has its own check.
    if status(root)['state'] != 'ready':
        raise SetupError('autostart_failed')
