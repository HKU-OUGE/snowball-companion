#!/usr/bin/env python3
"""Read-only cross-surface release checks; does not install, sync, or access HOME.

Run in the full development workspace. Pending characters are valid and reported
honestly. Optional --require-mirrors is intended only after packaging finishes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def check(project, require_mirrors=False):
    outputs = project / 'outputs'
    web = outputs / 'xueqiu-watch-preview'
    plugin = outputs / 'xueqiu-codex-marketplace/plugins/xueqiu-status'
    library = outputs / 'pet-library'
    errors, mirrors, checked = [], [], []
    def require(condition, message):
        if not condition:
            errors.append(message)
    def file_hash(path, expected):
        if not path.is_file():
            errors.append(f'Missing: {path.relative_to(project)}')
            return
        require(digest(path) == expected, f'Hash mismatch: {path.relative_to(project)}')
        checked.append(path)
    web_catalog = read(web / 'public/assets/pets/catalog.json')['pets']
    plugin_catalog = read(plugin / 'assets/pet/catalog.json')['characters']
    web_map = {p['id']: p for p in web_catalog}
    plugin_map = {p['id']: p for p in plugin_catalog}
    require(len(web_map) == len(web_catalog) == 14, 'Web must have exactly 14 distinct characters')
    require(set(web_map) == set(plugin_map), 'Web/plugin character IDs differ')
    ready = []
    for identifier, entry in plugin_map.items():
        require(web_map.get(identifier, {}).get('status') == entry['status'], f'{identifier}: readiness differs')
        if entry['status'] != 'ready':
            continue
        ready.append(identifier)
        manifest_path = plugin / 'assets/pet' / entry['manifest']
        manifest = read(manifest_path)
        atlas = manifest_path.parent / manifest['file']
        file_hash(atlas, manifest['sha256'])
        if atlas.is_file():
            data = atlas.read_bytes()
            require(data[:8] == b'\x89PNG\r\n\x1a\n' and struct.unpack('>II', data[16:24]) == (1536, 2288), f'{identifier}: invalid v2 PNG dimensions')
        require(manifest['frames_per_row'] == [6, 8, 8, 4, 5, 8, 6, 6, 6, 8, 8], f'{identifier}: incomplete frame contract')
        if identifier != 'snowball':
            web_manifest_path = web / 'public' / web_map[identifier]['manifest']
            web_manifest = read(web_manifest_path)
            require(web_manifest['sha256'] == manifest['sha256'], f'{identifier}: atlas manifests disagree')
            file_hash(web_manifest_path.parent / web_manifest['atlas'], manifest['sha256'])
            file_hash(library / identifier / 'final/spritesheet-extended.png', manifest['sha256'])
    audio = read(library / 'audio/manifest.json')
    require(set(audio['characters']) == set(web_map), 'Audio roster differs')
    cue_count = 0
    for identifier, character in audio['characters'].items():
        require(set(character['clips']) == {'complete', 'waiting', 'error'}, f'{identifier}: missing audio state')
        for cue, meta in character['clips'].items():
            cue_count += 1
            for folder in [library / 'audio', web / 'public/assets/audio/pets', plugin / 'assets/audio']:
                file_hash(folder / identifier / (cue + '.wav'), meta['sha256'])
    # Package copies may legitimately lag during creation; never call them current.
    for source, target in [(web, outputs / 'xueqiu-dev-kit/web-app'),
                           (plugin, outputs / 'xueqiu-dev-kit/codex-plugin/plugins/xueqiu-status')]:
        candidates = [p for p in checked if p.is_relative_to(source)]
        for path in candidates:
            mirror = target / path.relative_to(source)
            if not mirror.is_file() or digest(mirror) != digest(path):
                mirrors.append(str(mirror.relative_to(project)))
    if require_mirrors and mirrors:
        errors.append(f'{len(mirrors)} checked mirror assets are missing or stale')
    return {'ok': not errors, 'ready': ready,
            'pending': [p['id'] for p in plugin_catalog if p['status'] != 'ready'],
            'audio_cues': cue_count, 'checked_files': len(checked),
            'mirror_assets_current': not mirrors, 'stale_mirror_files': mirrors,
            'errors': errors,
            'not_checked': ['Cloud Pet installation/activation', 'Rendered browser UI', 'ESP32 SD installation and hardware playback', 'Human listening review']}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument('--require-mirrors', action='store_true')
    args = parser.parse_args()
    try:
        result = check(args.project, args.require_mirrors)
    except (OSError, ValueError, KeyError, struct.error) as exc:
        result = {'ok': False, 'errors': [str(exc)]}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if result['ok'] else 1)
