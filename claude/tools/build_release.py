#!/usr/bin/env python3
"""Reproducible source distribution; explicit allowlist, no user state."""
from pathlib import Path
import hashlib
import json
import stat
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT.parent / 'snowball-companion-claude-0.1.0.zip'


def selected():
    roots = [ROOT / '.claude-plugin', ROOT / 'plugins', ROOT / 'tests', ROOT / 'tools', ROOT / 'qa']
    paths = [ROOT / 'README.md', ROOT / 'VALIDATION.json', ROOT / 'runtime-sources.json']
    for folder in roots:
        if folder.exists():
            paths += [p for p in folder.rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError('Symlink not allowed in release: ' + str(path))
        if path.suffix in ('.pem', '.key', '.aesgcm', '.sqlite') or path.name in ('hub-token', 'ble-pairing.json', 'gateway.json', 'cloud.json'):
            raise ValueError('Private state is not a release asset')
        yield path


def main():
    files = list(selected())
    manifest = {str(p.relative_to(ROOT)): {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files}
    manifest_bytes = (json.dumps(manifest, indent=2, ensure_ascii=False) + '\n').encode()
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        content = [(str(p.relative_to(ROOT)), p.read_bytes()) for p in files] + [('FILES.sha256.json', manifest_bytes)]
        for name, data in content:
            info = zipfile.ZipInfo('snowball-claude-plugin/' + name, date_time=(2026, 10, 28, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
    with zipfile.ZipFile(OUT) as archive:
        assert archive.testzip() is None
        for name, meta in manifest.items():
            assert hashlib.sha256(archive.read('snowball-claude-plugin/' + name)).hexdigest() == meta['sha256']
    digest = hashlib.sha256(OUT.read_bytes()).hexdigest()
    OUT.with_suffix(OUT.suffix + '.sha256').write_text(digest + '  ' + OUT.name + '\n')
    print(json.dumps({'file': str(OUT), 'files': len(files) + 1, 'bytes': OUT.stat().st_size, 'sha256': digest}))


if __name__ == '__main__':
    main()
