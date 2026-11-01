#!/usr/bin/env python3
"""Exact manifest for the native installer, generated only after freezing."""
import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
import stat

MANIFEST = 'WINDOWS-BUNDLE.json'
VERSION = '0.6.0'
PRIVATE_NAMES = {'hub-token', 'ble-pairing.json', 'gateway.json', 'cloud.json', 'credentials.json', 'snapshot.json'}
BLOCKED_PARTS = {'firmware', 'web', 'cloud', '.git', 'node_modules', 'logs'}
BLOCKED_SUFFIXES = {'.key', '.pem', '.aesgcm', '.sqlite', '.sqlite3', '.db', '.log', '.jsonl', '.bin'}


def check_path(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Native distribution link/reparse point refused')


def check_name(name):
    path = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or path.is_absolute() or str(path) != name or '..' in path.parts:
        raise ValueError('Invalid native distribution path')
    if any(p in PRIVATE_NAMES or p in BLOCKED_PARTS or p.startswith('.env') for p in path.parts):
        raise ValueError('Private or unsupported native distribution path')
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        raise ValueError('Private or unsupported native distribution suffix')


def file_listing(directory):
    directory = Path(directory)
    check_path(directory)
    listing = {}
    for path in sorted(directory.rglob('*')):
        check_path(path)
        if not path.is_file():
            continue
        name = path.relative_to(directory).as_posix()
        if name == MANIFEST:
            continue
        check_name(name)
        data = path.read_bytes()
        listing[name] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    if not {'Snowball.exe', 'SnowballCLI.exe'} <= listing.keys():
        raise ValueError('Both native executables are required')
    return listing


def generate(directory):
    directory = Path(directory)
    target = directory/MANIFEST
    if target.exists():
        raise FileExistsError('Refusing to replace an existing native manifest')
    manifest = {'schema': 1, 'product': 'SnowballCompanion', 'version': VERSION,
                'files': file_listing(directory)}
    encoded = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)+'\n'
    if len(encoded.encode('utf-8')) > 2_000_000:
        raise ValueError('Native manifest exceeds installer limit')
    target.write_text(encoded, encoding='utf-8')
    return manifest


def verify(directory):
    directory = Path(directory)
    check_path(directory/MANIFEST)
    expected = json.loads((directory/MANIFEST).read_text(encoding='utf-8'))
    actual = {'schema': 1, 'product': 'SnowballCompanion', 'version': VERSION,
              'files': file_listing(directory)}
    if expected != actual:
        raise ValueError('Native distribution differs from its exact manifest')
    return expected


def archive(directory, target):
    directory, target = Path(directory).resolve(), Path(target).absolute()
    if target.is_relative_to(directory) or target.exists():
        raise ValueError('Archive must be a fresh file outside the distribution')
    manifest = verify(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.windows-zip-', dir=target.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as output:
            for name in sorted([*manifest['files'], MANIFEST]):
                output.write(directory/name, 'Snowball/'+name)
        with zipfile.ZipFile(temporary) as output:
            expected = {'Snowball/'+name for name in manifest['files']} | {'Snowball/'+MANIFEST}
            if len(output.namelist()) != len(expected) or set(output.namelist()) != expected:
                raise ValueError('ZIP does not match native manifest')
            if json.loads(output.read('Snowball/'+MANIFEST)) != manifest:
                raise ValueError('ZIP manifest changed')
            for name, entry in manifest['files'].items():
                data = output.read('Snowball/'+name)
                if entry != {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}:
                    raise ValueError('ZIP content changed: '+name)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'file': target.name, 'bytes': target.stat().st_size,
            'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    if args.archive:
        print(json.dumps(archive(args.directory, args.archive)))
        return
    result = verify(args.directory) if args.verify else generate(args.directory)
    print(json.dumps({'version': result['version'], 'files': len(result['files'])}))


if __name__ == '__main__': main()
