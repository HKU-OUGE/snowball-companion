"""Specialize the installed local plugin's Hooks for native Windows Python.

Called after the official plugin manager installs the local marketplace package.
Does not edit trust, global Hooks, account Pets, or any other plugin.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def configure(root, interpreter):
    root = Path(root).resolve(strict=True)
    interpreter = Path(interpreter).resolve(strict=True)
    manifest = json.loads((root / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))
    if manifest.get('name') != 'xueqiu-status':
        raise ValueError('Not the Snowball plugin')
    if interpreter.suffix.lower() != '.exe' or any(c in str(interpreter) for c in ('"', '\r', '\n', '$', '`')):
        raise ValueError('Unsupported interpreter path')
    target = root / 'hooks/hooks.json'
    if target.is_symlink():
        raise ValueError('Refusing a symlink Hooks file')
    config = json.loads(target.read_text(encoding='utf-8'))
    for groups in config['hooks'].values():
        for group in groups:
            for hook in group['hooks']:
                if hook.get('type') != 'command' or not hook.get('command', '').endswith('/scripts/codex_hook.py" hook'):
                    raise ValueError('Unexpected Hook definition; review before changing it')
                hook['command'] = '"' + interpreter.as_posix() + '" -X utf8 "${PLUGIN_ROOT}/scripts/codex_hook.py" hook'
    fd, temporary = tempfile.mkstemp(prefix='.windows-hooks-', dir=target.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(config['hooks'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plugin-root', type=Path, required=True)
    parser.add_argument('--python', type=Path, default=Path(sys.executable))
    args = parser.parse_args()
    if sys.platform != 'win32':
        parser.error('Windows only')
    count = configure(args.plugin_root, args.python)
    print(f'Configured {count} Snowball Hooks. Review changed commands in the host; trust was not modified.')


if __name__ == '__main__':
    main()
