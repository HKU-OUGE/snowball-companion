"""Resolve the recipient's registered console entry; never assume a developer URL."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


def resolve():
    # The full marketplace has companion next to plugins; cached plugins use a
    # per-user installation receipt written by the actual package installer.
    candidate = Path(__file__).resolve().parents[3] / 'companion'
    if (candidate / 'console_launcher.py').is_file():
        return candidate, sys.executable
    for anchor in list(Path(__file__).resolve().parents)[:6]:
        manifest = anchor / 'BUNDLE.json'
        if manifest.is_file() and not manifest.is_symlink():
            bundle = json.loads(manifest.read_text(encoding='utf-8'))
            if bundle.get('console_included') is True:
                candidate = anchor / ('codex/companion' if bundle.get('platform') == 'codex' else 'console/companion')
                if not candidate.is_symlink() and (candidate / 'console_launcher.py').is_file():
                    return candidate, sys.executable
    record = Path.home() / '.config/xueqiu/console-installation.json'
    if record.is_symlink():
        raise RuntimeError('Console installation receipt is linked; inspect it before opening.')
    value = json.loads(record.read_text(encoding='utf-8'))
    root = Path(value['runtime'])
    python = Path(value['python'])
    if (value.get('schema') != 1 or not root.is_absolute() or root.is_symlink() or
            not (root / 'console_launcher.py').is_file() or not python.is_file()):
        raise RuntimeError('Console installation is missing. Run the full package installer once.')
    return root, str(python)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--no-browser', action='store_true')
    operations=parser.add_mutually_exclusive_group()
    operations.add_argument('--setup', action='store_true')
    operations.add_argument('--setup-status', action='store_true')
    operations.add_argument('--setup-watch', action='store_true')
    operations.add_argument('--pet-state', choices=('working','verified','needs_auth','failed'))
    parser.add_argument('--character', default='auto')
    args = parser.parse_args()
    try:
        root, python = resolve()
        command = [python, '-X', 'utf8', str(root / 'console_launcher.py'), 'open']
        if args.setup_status or args.setup_watch or args.pet_state:
            command=[python,'-X','utf8',str(root/'setup_agent.py'),'pet' if args.pet_state else 'watch' if args.setup_watch else 'status']
            if args.pet_state:command+=['--state',args.pet_state,'--character',args.character]
        else:
            if args.no_browser:command.append('--no-browser')
            if args.setup:command+=['--setup','--adapter','cursor']
        return subprocess.run(command, cwd=root, check=False).returncode
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print('Open the full Snowball package and run Open-Snowball.cmd (Windows) or open-console.sh (Linux/macOS). Console entry unavailable: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
