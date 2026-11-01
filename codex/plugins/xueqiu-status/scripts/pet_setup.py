#!/usr/bin/env python3
"""Inspect a bundled character and remember account-verified Pets; no network calls."""
import argparse
from platform_support import flock, LOCK_EX, LOCK_NB, open_nofollow, private_file_ok, reject_links, protect_private
import hashlib
import http.client
import json
import os
import re
import struct
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRAMES = [6, 8, 8, 4, 5, 8, 6, 6, 6, 8, 8]
ID = re.compile(r'[a-z][a-z0-9-]{0,63}')


def safe_path(folder, relative):
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise ValueError('Invalid asset path')
    candidate = folder / part
    for path in [candidate, *candidate.parents]:
        if path.is_symlink():
            raise ValueError('Refusing symlink asset or state path')
    return candidate


def catalog(root=ROOT):
    value = json.loads((root / 'assets/pet/catalog.json').read_text())
    if value.get('schema_version') != 1:
        raise ValueError('Unsupported character catalog')
    ids = set()
    for character in value['characters']:
        key = character['id']
        if not ID.fullmatch(key) or key in ids:
            raise ValueError('Invalid or duplicate character ID')
        ids.add(key)
        if character['status'] not in ('ready', 'pending'):
            raise ValueError('Invalid character availability')
    if value['default_character'] not in ids:
        raise ValueError('Unknown default character')
    return value


def character_info(character='snowball', root=ROOT):
    for entry in catalog(root)['characters']:
        if entry['id'] == character:
            return entry
    raise ValueError('Unknown character: ' + character)


def asset(root=ROOT, character='snowball'):
    entry = character_info(character, root)
    if entry['status'] != 'ready':
        raise ValueError('Character artwork is pending; no installable atlas: ' + character)
    manifest = safe_path(root / 'assets/pet', entry['manifest'])
    meta = json.loads(manifest.read_text())
    image = safe_path(manifest.parent, meta['file'])
    data = image.read_bytes()
    if hashlib.sha256(data).hexdigest() != meta['sha256']:
        raise ValueError('Pet asset checksum mismatch')
    if (len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n' or
            struct.unpack('!II', data[16:24]) != (1536, 2288)):
        raise ValueError('Unsupported Pet atlas dimensions')
    if meta.get('version') != 2 or meta.get('frames_per_row') != FRAMES:
        raise ValueError('Incomplete v2 animation contract')
    if (meta.get('width'), meta.get('height')) != (1536, 2288):
        raise ValueError('Invalid manifest dimensions')
    return dict(meta, character=character, file=str(image.resolve()))


def state_path():
    home = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser()
    return home / 'state/plugins/xueqiu-status/pet-install.json'


def read_state(path):
    safe_path(path.parent, path.name)
    if not path.exists():
        return {'schema_version': 2, 'characters': {}, 'selected_character': None, 'selected_revision': 0}
    value = json.loads(path.read_text())
    # A v0.3 single-Pet receipt is Snowball. Migration is in memory on reads;
    # the first authenticated record writes v2 without losing this receipt.
    if 'schema_version' not in value and 'pet_id' in value:
        return {'schema_version': 2, 'characters': {'snowball': value},
                'selected_character': 'snowball', 'selected_revision': 0}
    if value.get('schema_version') != 2 or not isinstance(value.get('characters'), dict):
        raise ValueError('Unsupported private Pet state; refusing to overwrite')
    return value


def record(pet_id, meta, path, selected=False):
    if not isinstance(pet_id, str) or not re.fullmatch(r'pet_[A-Za-z0-9_-]{1,100}', pet_id):
        raise ValueError('Expected a verified custom Pet ID')
    character = meta.get('character', 'snowball')
    if not ID.fullmatch(character):
        raise ValueError('Invalid character ID')
    safe_path(path.parent, path.name)
    reject_links(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    protect_private(path.parent)
    lock = safe_path(path.parent, path.name + '.lock')
    lock_fd = open_nofollow(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        flock(lock_fd, LOCK_EX)
        result = read_state(path)
        result['characters'][character] = {
            'pet_id': pet_id, 'asset_key': meta['asset_key'], 'sha256': meta['sha256'],
            'note': 'Always verify current account ownership with list_pets before reuse.'}
        # A receipt does not prove the host has activated this character.
        if selected:
            if result.get('selected_character') != character:
                result['selected_revision'] = result.get('selected_revision', 0) + 1
            result['selected_character'] = character
        fd, tmp = tempfile.mkstemp(prefix='.pet-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            protect_private(tmp)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return result
    finally:
        os.close(lock_fd)


def inspect(character, root=ROOT, path=None):
    path = state_path() if path is None else path
    info = character_info(character, root)
    state = read_state(path)
    meta = asset(root, character) if info['status'] == 'ready' else None
    return {'character': info, 'asset': meta,
            'saved_install': state['characters'].get(character),
            'last_verified_selected_character': state.get('selected_character'),
            'selected_revision': state.get('selected_revision', 0),
            'state_path': str(path),
            'installation_mode': 'host onboarding + authenticated Pets MCP; no silent install hook'}


def sync(character, root=ROOT, token_path=None):
    """Notify the documented local Hub; this cannot activate a desktop Pet."""
    asset(root, character)
    path = token_path or Path.home() / '.config/xueqiu/hub-token'
    safe_path(path.parent, path.name)
    if os.name == 'nt' and not private_file_ok(path):
        raise ValueError('Hub token must have a private user ACL')
    with os.fdopen(open_nofollow(path, os.O_RDONLY), 'r', encoding='utf-8') as stream:
        token = stream.read().strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        raise ValueError('Invalid Hub token file')
    connection = http.client.HTTPConnection('127.0.0.1', 4190, timeout=2)
    try:
        connection.request('POST', '/v1/pet', json.dumps({'character_id': character}),
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = response.read(16385)
        if response.status != 200 or len(body) > 16384:
            raise ValueError('Hub did not accept character selection')
        result = json.loads(body)
        confirmed = result.get('pet', result)
        if confirmed.get('character_id') != character or type(confirmed.get('revision')) is not int:
            raise ValueError('Hub did not confirm the requested character')
        return {'hub_selection': confirmed,
                'device_verified': False,
                'note': 'Hub selection accepted; device delivery and desktop activation require separate verification.'}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['list', 'inspect', 'record', 'sync'])
    parser.add_argument('--character', default='snowball')
    parser.add_argument('--pet-id')
    parser.add_argument('--token-file', type=Path)
    parser.add_argument('--selected', action='store_true',
                        help='Only after select_pet confirms this active Pet ID')
    args = parser.parse_args()
    try:
        if args.action == 'list':
            result = catalog()
            # A claimed-ready entry with missing/corrupt art must never appear
            # installable. Keep the catalog readable while exposing the error.
            for entry in result['characters']:
                entry['installable'] = False
                if entry['status'] == 'ready':
                    try:
                        asset(ROOT, entry['id'])
                        entry['installable'] = True
                    except (ValueError, OSError, KeyError) as error:
                        entry['asset_error'] = str(error)
        elif args.action == 'sync':
            result = sync(args.character, token_path=args.token_file)
        elif args.action == 'record':
            if not args.pet_id:
                parser.error('--pet-id is required after MCP ownership verification')
            result = record(args.pet_id, asset(ROOT, args.character), state_path(), args.selected)
        else:
            result = inspect(args.character)
    except (ValueError, OSError, KeyError, http.client.HTTPException) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
