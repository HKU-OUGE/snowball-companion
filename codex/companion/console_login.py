"""Inspect and enable current-user Snowball login services without adopting foreign units."""
import json
import os
from pathlib import Path
import subprocess
import sys

from console_backend import SetupError, child_options, interpreter
from private_files import checked_path


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
