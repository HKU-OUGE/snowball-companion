#!/usr/bin/env python3
"""Stage only the reviewed Codex bundle allowlist; never discovers user files."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from build_plugin_bundles import bundle_inputs, validate_name

ROOT = Path(__file__).resolve().parents[2]
REQUIRED = {
    'codex/companion/snowball_windows.py', 'codex/companion/windows_gui.py',
    'codex/companion/windows_install.py', 'codex/companion/windows_runtime.py',
    'codex/companion/platform_support.py', 'codex/companion/pairing_store.py',
    'codex/companion/device_setup.py', 'codex/companion/connection_diagnostics.py',
    'codex/plugins/xueqiu-status/scripts/codex_hook.py',
    'codex/plugins/xueqiu-status/scripts/windows_hook.ps1',
    'codex/plugins/xueqiu-status/hooks/hooks.json',
    'codex/plugins/xueqiu-status/scripts/platform_support.py',
    'codex/tools/platform_support.py', 'codex/tools/install_plugin.py',
}


def stage(root, output):
    files = bundle_inputs(Path(root), 'codex')
    missing = REQUIRED - files.keys()
    if missing:
        raise ValueError('Windows source missing from reviewed allowlist: ' + ', '.join(sorted(missing)))
    for name in files:
        validate_name(name)
    output = Path(output)
    # A fresh directory prevents stale files from entering the next build.
    output.mkdir(parents=True, exist_ok=False)
    for name, data in sorted(files.items()):
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    listing = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
               for name, data in sorted(files.items())}
    (output/'FILES.sha256.json').write_text(json.dumps(listing, sort_keys=True, indent=2)+'\n', encoding='utf-8')
    return listing


def verify(output):
    output = Path(output)
    listing = json.loads((output/'FILES.sha256.json').read_text(encoding='utf-8'))
    actual = {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()}
    if actual != set(listing) | {'FILES.sha256.json'}:
        raise ValueError('Payload contains unlisted or missing files')
    for name, expected in listing.items():
        validate_name(name)
        path = output/name
        if path.is_symlink():
            raise ValueError('Payload symlink refused')
        data = path.read_bytes()
        if expected != {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}:
            raise ValueError('Payload digest mismatch: ' + name)
    if not REQUIRED <= listing.keys():
        raise ValueError('Incomplete Windows payload')
    return listing


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    files = verify(args.output) if args.verify else stage(args.root, args.output)
    print(json.dumps({'files': len(files), 'firmware_included': False, 'credentials_included': False}))


if __name__ == '__main__': main()
