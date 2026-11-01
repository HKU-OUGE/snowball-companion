#!/usr/bin/env python3
"""Install only Snowball through Codex, preserving missing old hook cache files."""
import argparse
import datetime
from platform_support import flock, LOCK_EX, LOCK_NB, open_nofollow, private_file_ok, reject_links, protect_private
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

PLUGIN = 'xueqiu-status@xueqiu-local'


def private_dir(path):
    reject_links(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError(f'Refusing symlink directory: {path}')
    protect_private(path)


def checked_path(root, relative):
    current = root
    for part in Path(relative).parts:
        if part in ('.', '..'):
            raise RuntimeError('Invalid cache path')
        current = current / part
        if current.is_symlink():
            raise RuntimeError(f'Refusing symlink: {current}')
    return current


def backup_hooks(cache_root, backup_root):
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-')
    backup = Path(tempfile.mkdtemp(prefix=stamp, dir=backup_root))
    records = []
    if cache_root.is_symlink():
        raise RuntimeError(f'Refusing symlink cache: {cache_root}')
    if cache_root.exists():
        for version in sorted(cache_root.iterdir()):
            if version.is_symlink():
                raise RuntimeError(f'Refusing symlink version: {version}')
            if not version.is_dir():
                continue
            for folder in ('scripts', 'hooks'):
                source = checked_path(cache_root, Path(version.name) / folder)
                if not source.exists():
                    continue
                for item in sorted(source.rglob('*')):
                    relative = item.relative_to(cache_root)
                    checked_path(cache_root, relative)
                    if not item.is_file():
                        continue
                    data = item.read_bytes()
                    target = backup / relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_bytes(data)
                    protect_private(target)
                    records.append({'path': str(relative), 'mode': item.stat().st_mode & 0o777,
                                    'sha256': hashlib.sha256(data).hexdigest()})
    (backup / 'manifest.json').write_text(json.dumps({'plugin': PLUGIN, 'cache_root': str(cache_root),
                                                    'files': records}, indent=2) + '\n')
    protect_private(backup / 'manifest.json')
    return backup, records


def restore_missing(cache_root, backup, records):
    restored = 0
    for record in records:
        target = checked_path(cache_root, record['path'])
        if target.exists():
            continue  # Never replace the new installation or change trusted definitions.
        data = (backup / record['path']).read_bytes()
        if hashlib.sha256(data).hexdigest() != record['sha256']:
            raise RuntimeError(f'Backup checksum mismatch: {record["path"]}')
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        checked_path(cache_root, record['path'])
        fd, temporary = tempfile.mkstemp(prefix='.xueqiu-restore-', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, record['mode'])
            try:
                os.link(temporary, target)  # Atomic creation without overwriting any file.
                restored += 1
            except FileExistsError:
                pass
        finally:
            os.unlink(temporary)
    return restored


def interrupted(signum, _frame):
    raise InterruptedError(f'Installer interrupted by signal {signum}')


def install(codex, market):
    os.umask(0o077)
    codex_home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser()
    cache_root = codex_home / 'plugins/cache/xueqiu-local/xueqiu-status'
    state_home = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))).expanduser()
    backup_root = state_home / 'xueqiu/plugin-upgrade-backups'
    private_dir(backup_root)
    with os.fdopen(open_nofollow(backup_root / 'install.lock', os.O_CREAT | os.O_RDWR), 'a') as lock:
        flock(lock.fileno(), LOCK_EX)
        backup, records = backup_hooks(cache_root, backup_root)
        print(f'Old hook files preserved privately: {backup}', flush=True)
        handlers = {sig: signal.signal(sig, interrupted) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            subprocess.run([codex, 'plugin', 'marketplace', 'add', str(market), '--json'], check=True)
            subprocess.run([codex, 'plugin', 'add', PLUGIN, '--json'], check=True)
        finally:
            # Run in this same outer process: missing PreToolUse hooks must not prevent repair.
            for sig in handlers:
                signal.signal(sig, signal.SIG_IGN)
            try:
                count = restore_missing(cache_root, backup, records)
                print(f'Restored {count} missing old hook files; existing files were not replaced.', flush=True)
            finally:
                for sig, handler in handlers.items():
                    signal.signal(sig, handler)
    print('Snowball Companion installed. Reload the plugin when convenient; run its Setup action to install/activate Snowball Pet. Review new or changed hooks in /hooks if requested. Package installation alone does not create an account Pet.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True)
    parser.add_argument('--market-root', type=Path, required=True)
    args = parser.parse_args()
    try:
        install(args.codex, args.market_root.resolve())
    except subprocess.CalledProcessError as error:
        print(f'Codex installation failed (exit {error.returncode}); old hook restoration was attempted.', file=sys.stderr)
        return error.returncode if error.returncode > 0 else 1
    except (OSError, RuntimeError) as error:
        print(f'Installation stopped: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
