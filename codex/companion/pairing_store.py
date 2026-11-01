"""Version-1 pairing API; user-scoped DPAPI at rest on native Windows.

No secret values or raw platform exceptions are included in public errors.
Linux retains the existing private v1 JSON. Windows legacy JSON is migrated
under a store lock only after validation, encryption and decryption checks.
"""
import base64
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import re
import tempfile

import platform_support as platform

WINDOWS = os.name == 'nt'
DEFAULT_PATH = Path.home() / '.config/xueqiu/ble-pairing.json'
FORMAT = 'snowball-companion-pairing-dpapi'
DOMAIN = b'Snowball Companion pairing v1\x00current-user'
DESCRIPTION = 'Snowball Companion pairing v1'
MAX_BYTES = 16384


class PairingStoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate(config):
    if not isinstance(config, dict) or set(config) != {'v', 'device', 'key'}:
        raise PairingStoreError('invalid_credentials')
    if type(config['v']) is not int or config['v'] != 1:
        raise PairingStoreError('invalid_credentials')
    if not isinstance(config['device'], str) or not re.fullmatch(r'Xueqiu-[0-9a-f]{8}', config['device']):
        raise PairingStoreError('invalid_credentials')
    if not isinstance(config['key'], str) or not re.fullmatch(r'[0-9a-fA-F]{64}', config['key']):
        raise PairingStoreError('invalid_credentials')
    return {'v': 1, 'device': config['device'], 'key': config['key'].lower()}


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PairingStoreError('invalid_credentials')
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError):
        raise PairingStoreError('invalid_credentials') from None


def _dpapi(data, decrypt=False):
    # No LOCAL_MACHINE flag: only the current Windows user can decrypt.
    # The fixed entropy and encrypted format marker separate this data from
    # unrelated applications' DPAPI blobs; neither is a secret.
    if os.name != 'nt':
        raise PairingStoreError('dpapi_unavailable')
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    common = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
              ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = common
    function.restype = wintypes.BOOL
    source = ctypes.create_string_buffer(data)
    entropy = ctypes.create_string_buffer(DOMAIN)
    incoming = Blob(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_ubyte)))
    extra = Blob(len(DOMAIN), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    description = ctypes.c_void_p()
    label = ctypes.create_unicode_buffer(DESCRIPTION)
    try:
        second = ctypes.byref(description) if decrypt else ctypes.cast(label, ctypes.c_void_p)
        if not function(ctypes.byref(incoming), second, ctypes.byref(extra), None, None, 1, ctypes.byref(output)):
            raise PairingStoreError('dpapi_decrypt_failed' if decrypt else 'dpapi_encrypt_failed')
        if decrypt and (not description.value or ctypes.wstring_at(description) != DESCRIPTION):
            raise PairingStoreError('invalid_protected_credentials')
        return ctypes.string_at(output.data, output.size)
    finally:
        ctypes.memset(source, 0, len(data))
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel.LocalFree(output.data)
        if description.value:
            kernel.LocalFree(description)


def _encode(config):
    if not WINDOWS:
        return (json.dumps(config, separators=(',', ':')) + '\n').encode()
    plain = json.dumps({'format': FORMAT, 'config': config}, separators=(',', ':')).encode()
    encrypted = _dpapi(plain)
    if _dpapi(encrypted, True) != plain:
        raise PairingStoreError('dpapi_verification_failed')
    return (json.dumps({'format': FORMAT, 'version': 1, 'scope': 'current-user',
                        'blob': base64.b64encode(encrypted).decode('ascii')}, separators=(',', ':')) + '\n').encode()


def _decode(value):
    if isinstance(value, dict) and set(value) == {'v', 'device', 'key'}:
        return validate(value), True
    if not isinstance(value, dict) or set(value) != {'format', 'version', 'scope', 'blob'} or \
            value.get('format') != FORMAT or type(value.get('version')) is not int or \
            value['version'] != 1 or value.get('scope') != 'current-user' or not isinstance(value.get('blob'), str):
        raise PairingStoreError('invalid_protected_credentials')
    if not WINDOWS:
        raise PairingStoreError('dpapi_unavailable')
    try:
        encrypted = base64.b64decode(value['blob'], validate=True)
    except (ValueError, TypeError):
        raise PairingStoreError('invalid_protected_credentials') from None
    inner = _json(_dpapi(encrypted, True))
    if not isinstance(inner, dict) or set(inner) != {'format', 'config'} or inner['format'] != FORMAT:
        raise PairingStoreError('invalid_protected_credentials')
    return validate(inner['config']), False


@contextmanager
def _store_lock(path):
    platform.ensure_private_dir(path.parent)
    fd = platform.open_nofollow(path.with_name(path.name + '.lock'), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        platform.protect_private(path.with_name(path.name + '.lock'))
        platform.flock(fd, platform.LOCK_EX)
        try:
            yield
        finally:
            platform.flock(fd, platform.LOCK_UN)
    finally:
        os.close(fd)


def _read(path, repair_acl=False):
    if not platform.private_file_ok(path):
        if not path.exists():
            raise PairingStoreError('missing_credentials')
        if not WINDOWS or not repair_acl:
            raise PairingStoreError('credentials_not_private')
        platform.protect_private(path)
        if not platform.private_file_ok(path):
            raise PairingStoreError('credentials_not_private')
    fd = platform.open_nofollow(path, os.O_RDONLY)
    with os.fdopen(fd, 'rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise PairingStoreError('invalid_credentials')
    return _decode(_json(raw))


def _save(config, path):
    platform.reject_links(path)
    if path.exists() and not platform.private_file_ok(path):
        raise PairingStoreError('credentials_not_private')
    encoded = _encode(config)  # Complete all fallible crypto before replacing.
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    temporary = Path(temporary)
    try:
        platform.protect_private(temporary)
        if not platform.private_file_ok(temporary):
            raise PairingStoreError('credentials_not_private')
        with os.fdopen(fd, 'wb') as stream:
            fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        platform.reject_links(path)
        os.replace(temporary, path)
    finally:
        if fd is not None:
            os.close(fd)
        temporary.unlink(missing_ok=True)


def save(config, path=DEFAULT_PATH):
    config, path = validate(config), Path(path)
    try:
        with _store_lock(path):
            _save(config, path)
    except PairingStoreError:
        raise
    except OSError:
        raise PairingStoreError('credential_write_failed') from None


def load(path=DEFAULT_PATH, *, migrate=True):
    path = Path(path)
    try:
        if not path.exists():
            raise PairingStoreError('missing_credentials')
        if WINDOWS and migrate:
            with _store_lock(path):
                config, legacy = _read(path, repair_acl=True)
                if legacy:
                    _save(config, path)
                return config
        return _read(path)[0]
    except PairingStoreError:
        raise
    except OSError:
        raise PairingStoreError('credential_read_failed') from None
