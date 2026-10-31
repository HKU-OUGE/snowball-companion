#!/usr/bin/env python3
"""Build reviewed, deterministic plugin-only ZIPs. Never installs or publishes.

Every input is named in plugin_bundle_files.json; no recursive file discovery.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
RELEASE = '0.5.0'
VERSIONS = {'codex': '0.5.0', 'claude': '0.2.0', 'cursor': '0.2.0'}
LABELS = {'codex': 'Codex', 'claude': 'Claude', 'cursor': 'Cursor'}
FIXED_TIME = (2026, 11, 1, 0, 0, 0)
CORE_LIMIT = 8_000_000
MANIFEST = 'tools/plugin_bundle_files.json'
COMMON = {'AGENTS.md', 'docs/INSTALLATION.md', 'docs/START-HERE.md', 'docs/UPDATES.md'}
PRIVATE_NAMES = {'hub-token', 'ble-pairing.json', 'gateway.json', 'cloud.json', 'credentials.json', 'snapshot.json', '.env'}
BLOCKED_PARTS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', 'qa', 'work', 'logs', 'firmware', 'web', 'cloud', 'dist', 'build'}
BLOCKED_SUFFIXES = {'.key', '.pem', '.aesgcm', '.sqlite', '.sqlite3', '.db', '.log', '.jsonl', '.pyc', '.bin', '.zip'}
PRIVATE_TEXT = (
    re.compile(rb'BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY'),
    re.compile(rb'/(?:home|Users)/[A-Za-z0-9_.-]+/'),
    re.compile(rb'\b(?:ghp_|github_pat_|sk-proj-|sk-ant-)[A-Za-z0-9_-]{16,}'),
    re.compile(rb'''(?i)(?:account_id|zone_id)\s*[=:]\s*['"][a-f0-9]{32}['"]'''),
    re.compile(rb'''(?i)\b(?:password|passwd|secret|api_key|access_token|pairing_key|device_token)\s*[:=]\s*['"][^'"{}\n]{6,}['"]'''),
)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()


def validate_name(name):
    if not isinstance(name, str) or not name or '\\' in name:
        raise ValueError('Invalid release path')
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or str(path) != name:
        raise ValueError('Invalid release path')
    if any(p in BLOCKED_PARTS or p in PRIVATE_NAMES or p.startswith('.env') for p in path.parts):
        raise ValueError('Private or unsupported release path: ' + name)
    if path.suffix.lower() in BLOCKED_SUFFIXES:
        raise ValueError('Private or unsupported release suffix: ' + name)
    return path


def read_source(root, name):
    path = validate_name(name)
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink is not a release source: ' + name)
    if not current.is_file():
        raise ValueError('Missing release input: ' + name)
    data = current.read_bytes()
    if path.suffix not in ('.png', '.wav'):
        for pattern in PRIVATE_TEXT:
            if pattern.search(data):
                # Report a filename, never the matched secret or private path.
                raise ValueError('Private data pattern in release input: ' + name)
    return data


def source_lists(root):
    value = json.loads(read_source(root, MANIFEST))
    if set(value) != {'schema', 'common', 'platforms'} or value['schema'] != 1:
        raise ValueError('Invalid source manifest')
    if not isinstance(value['common'], list) or not isinstance(value['platforms'], dict):
        raise ValueError('Invalid source manifest lists')
    if set(value['platforms']) != set(VERSIONS) or set(value['common']) != COMMON:
        raise ValueError('Unreviewed common or platform inputs')
    for platform, names in value['platforms'].items():
        if not isinstance(names, list) or any(not isinstance(n, str) or not n.startswith(platform + '/') for n in names):
            raise ValueError('Cross-platform source path')
        paths = value['common'] + names
        if len(paths) != len(set(paths)):
            raise ValueError('Duplicate source path')
        for name in paths:
            validate_name(name)
    return value


def core_keep(name):
    assets = 'codex/plugins/xueqiu-status/assets/'
    return not name.startswith(assets) or name[len(assets):] in {
        'icon.png', 'pet/catalog.json', 'pet/manifest.json', 'pet/snowball.png'}


def bundle_inputs(root, platform, core=False):
    if platform not in VERSIONS or (core and platform != 'codex'):
        raise ValueError('Invalid bundle selection')
    manifest = source_lists(root)
    names = manifest['common'] + manifest['platforms'][platform]
    files = {name: read_source(root, name) for name in names if not core or core_keep(name)}
    plugin = {'codex': 'codex/plugins/xueqiu-status/.codex-plugin/plugin.json',
              'claude': 'claude/plugins/snowball-companion/.claude-plugin/plugin.json',
              'cursor': 'cursor/snowball-companion/.cursor-plugin/plugin.json'}[platform]
    if json.loads(files[plugin]).get('version') != VERSIONS[platform]:
        raise ValueError('Plugin version does not match release')
    if core:
        name = 'codex/plugins/xueqiu-status/assets/pet/catalog.json'
        catalog = json.loads(files[name])
        catalog['characters'] = [c for c in catalog['characters'] if c['id'] == 'snowball']
        if len(catalog['characters']) != 1 or catalog['default_character'] != 'snowball':
            raise ValueError('Missing default Snowball character')
        files[name] = encoded(catalog)
        files['codex/README.md'] = (
            '# Snowball Companion · Codex Core 0.5.0\n\n'
            '本包仅含雪球基础 Pet、Codex Hooks 和电脑伴随运行时，不含其他角色、音频或网页。\n\n'
            '进入本目录执行 `./install.sh`，新开聊天后运行插件 Setup；账号 Pet 仍需宿主 Pets 服务验证。\n\n'
            '[新手指南](../docs/START-HERE.md) · [安装与卸载](../docs/INSTALLATION.md) · [设备更新](../docs/UPDATES.md)\n\n'
            '使用更多角色时，用完整版升级同一个 `xueqiu-status@xueqiu-local` 插件，不并排安装两份。\n'
        ).encode()
        files['codex/plugins/xueqiu-status/README.md'] = (
            '# Snowball Companion · Core\n\n'
            '本包仅列出并包含雪球基础 Pet。用 `python3 scripts/pet_setup.py list` 核对角色，'
            '再通过 Setup 和宿主 Pets 服务创建或复用账号 Pet。没有其他角色或音频；'
            '改用完整版可扩展角色，不改变插件 ID 或接收者配对数据。\n'
        ).encode()
    label = LABELS[platform] + ('-Core' if core else '')
    files['README.md'] = (
        f'# Snowball Companion · {label}\n\n分发版本 {RELEASE}；平台插件版本 {VERSIONS[platform]}。\n\n'
        f'先看 [新手指南](docs/START-HERE.md)，再进入 [{platform}/]({platform}/README.md) 安装。\n\n'
        '本包不安装或更新设备固件。USB 安装盘需要相应固件，不能凭此 ZIP 声称已部署。\n\n'
        '文件完整性见 FILES.sha256.json；账号授权、配对及依赖由接收者完成。\n'
    ).encode()
    files['BUNDLE.json'] = encoded({'schema': 1, 'release': RELEASE, 'platform': platform,
        'plugin_version': VERSIONS[platform], 'flavor': 'core' if core else 'full',
        'firmware_included': False, 'credentials_included': False, 'usb_storage_firmware_included': False})
    return files


def mode_for(name):
    return 0o755 if name.endswith('.sh') else 0o644


def verify_archive(path):
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or not names:
            raise ValueError('Duplicate or empty archive')
        roots = {n.split('/', 1)[0] for n in names}
        if len(roots) != 1:
            raise ValueError('Archive must have one root')
        prefix = next(iter(roots)) + '/'
        listing = json.loads(archive.read(prefix + 'FILES.sha256.json'))
        if set(names) != {prefix+n for n in listing} | {prefix+'FILES.sha256.json'}:
            raise ValueError('Archive file list does not match manifest')
        for name, entry in listing.items():
            validate_name(name)
            content = archive.read(prefix + name)
            info = archive.getinfo(prefix + name)
            if entry != {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content), 'mode': mode_for(name)}:
                raise ValueError('Archive digest or size mismatch: ' + name)
            if stat.S_IMODE(info.external_attr >> 16) != mode_for(name) or info.date_time != FIXED_TIME:
                raise ValueError('Archive metadata mismatch: ' + name)
        if archive.testzip() is not None:
            raise ValueError('Archive CRC failure')
    return len(listing)


def build(root, out, platform, core=False):
    root, out = Path(root), Path(out)
    files = bundle_inputs(root, platform, core)
    label = 'Snowball-' + LABELS[platform] + ('-Core' if core else '')
    listing = {name: {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data), 'mode': mode_for(name)}
               for name, data in sorted(files.items())}
    files['FILES.sha256.json'] = encoded(listing)
    out.mkdir(parents=True, exist_ok=True)
    target = out / (label + '.zip')
    fd, temporary = tempfile.mkstemp(prefix='.bundle-', dir=out)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(label + '/' + name, FIXED_TIME)
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | mode_for(name)) << 16
                archive.writestr(info, data, compresslevel=9)
        verify_archive(temporary)
        size = Path(temporary).stat().st_size
        if core and size >= CORE_LIMIT:
            raise ValueError('Codex Core ZIP exceeds 8,000,000-byte limit')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.zip.sha256').write_text(digest + '  ' + target.name + '\n')
    return {'file': target.name, 'bytes': size, 'sha256': digest, 'files': len(files),
            'platform': platform, 'flavor': 'core' if core else 'full'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT.parent / ('snowball-plugin-bundles-v' + RELEASE))
    parser.add_argument('--platform', choices=('all', *VERSIONS), default='all')
    parser.add_argument('--include-core', action='store_true')
    args = parser.parse_args(argv)
    platforms = tuple(VERSIONS) if args.platform == 'all' else (args.platform,)
    results = [build(ROOT, args.output, p) for p in platforms]
    if args.include_core:
        results.append(build(ROOT, args.output, 'codex', core=True))
    (args.output / 'SHA256SUMS').write_text(''.join(r['sha256']+'  '+r['file']+'\n' for r in results))
    (args.output / 'bundles.json').write_bytes(encoded({'release': RELEASE, 'bundles': results}))
    print(json.dumps({'release': RELEASE, 'bundles': results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
