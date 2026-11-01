"""Snowball networking: separate device credential, sealed BLE provisioning.

No ZeroTier identity, Hub token, or Wi-Fi password is placed in a URL or log.
The desktop gateway cert is pinned by a private CA delivered over the paired
BLE channel. Device persistent storage requires a separately provisioned HMAC
key; RAM-only mode remains explicitly labelled until then.
"""
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import struct
import time
import tempfile
from urllib.parse import urlsplit

NETWORK = '9e7a0006-6ed4-4b6b-bf37-43dc42838c20'
MAX_CONFIG = 6000


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def private_write(path, content):
    from platform_support import ensure_private_dir, protect_private, reject_links
    path = Path(path)
    ensure_private_dir(path.parent)
    reject_links(path)
    fd, temp = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            protect_private(temp)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        reject_links(path)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def endpoint(value):
    if not isinstance(value, str) or len(value) > 191:
        raise ValueError('Invalid endpoint')
    u = urlsplit(value)
    if (u.scheme != 'https' or not u.hostname or u.username or u.password or
            u.query or u.fragment or u.path not in ('', '/') or not u.port):
        raise ValueError('Endpoint must be HTTPS with explicit port and no path')
    if not re.fullmatch(r'[A-Za-z0-9.-]+', u.hostname):
        raise ValueError('Invalid gateway hostname')
    return value.rstrip('/')


def validate_config(config):
    if not isinstance(config, dict) or set(config) != {'v', 'mode', 'ssid', 'password', 'lan', 'remote', 'ca', 'device', 'access', 'issued'}:
        raise ValueError('Invalid configuration fields')
    if config['v'] != 1 or type(config['mode']) is not int or config['mode'] not in (1, 2):
        raise ValueError('Invalid network mode')
    for name, low, high in [('ssid', 1, 32), ('password', 8, 63), ('device', 1, 31)]:
        s = config[name]
        if not isinstance(s, str) or not low <= len(s.encode()) <= high or any(ord(c) < 32 for c in s):
            raise ValueError('Invalid ' + name)
    endpoint(config['lan'])
    if config['remote']:
        endpoint(config['remote'])
    if (not isinstance(config['ca'], str) or not 128 <= len(config['ca']) <= 3000 or
            not config['ca'].startswith('-----BEGIN CERTIFICATE-----')):
        raise ValueError('Invalid CA certificate')
    if not re.fullmatch(r'[0-9a-f]{64}', config['access']):
        raise ValueError('Invalid device credential')
    if type(config['issued']) is not int or not 1735689600 <= config['issued'] <= 4102444800:
        raise ValueError('Invalid clock')
    if len(encode(config)) + 28 > MAX_CONFIG:
        raise ValueError('Configuration too large')
    return config


def seal_config(config, pairing_key, nonce, iv=None):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    validate_config(config)
    nonce = bytes.fromhex(nonce) if isinstance(nonce, str) else nonce
    if len(pairing_key) != 32 or len(nonce) != 8:
        raise ValueError('Invalid pairing material')
    key = hmac.digest(pairing_key, b'XQ-PROVISION-v1' + nonce, 'sha256')
    iv = os.urandom(12) if iv is None else iv
    return iv + AESGCM(key).encrypt(iv, encode(config), b'XQNET1' + nonce)


def packets(blob, key, info):
    if not 29 <= len(blob) <= MAX_CONFIG:
        raise ValueError('Invalid sealed configuration length')
    pieces = [(0, struct.pack('<H', len(blob)))]
    pieces += [(1, struct.pack('<H', i) + blob[i:i+180]) for i in range(0, len(blob), 180)]
    pieces += [(2, b'')]
    for index, (op, data) in enumerate(pieces, info['accepted'] + 1):
        body = struct.pack('<BBBI8s', 9, 0, op, index, bytes.fromhex(info['nonce'])) + data
        yield body + hmac.digest(key, body, 'sha256')


def verify_status(raw, key, nonce):
    envelope = json.loads(raw)
    data = envelope['data']
    proof = hmac.digest(key, b'XQNETSTATUS' + bytes.fromhex(nonce) + encode(data), 'sha256').hex()
    if not hmac.compare_digest(proof, str(envelope.get('h', ''))):
        raise ValueError('Network status authentication failed')
    return data


def request_proof(key, path, raw):
    return hmac.digest(key, path.encode() + b'\n' + raw, 'sha256').hex()


def create_gateway(root, bind, device):
    """Create private gateway identity once; never silently rotate an enrolled key."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    root = Path(root)
    config_path = root / 'gateway.json'
    if config_path.exists():
        current = json.loads(config_path.read_text())
        if current['bind'] != bind or current['device'] != device:
            raise ValueError('Existing gateway identity differs; explicit migration required')
        return current
    ip = ipaddress.ip_address(bind)
    if not ip.is_private or ip.is_loopback or ip.is_unspecified:
        raise ValueError('Choose a specific private LAN interface')
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Snowball local gateway')])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip)]), critical=False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(key, hashes.SHA256()))
    private_write(root / 'server-key.pem', key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    private_write(root / 'server-cert.pem', cert.public_bytes(serialization.Encoding.PEM))
    result = {'v': 1, 'bind': bind, 'port': 4193, 'device': device, 'access': os.urandom(32).hex()}
    private_write(config_path, encode(result))
    return result
