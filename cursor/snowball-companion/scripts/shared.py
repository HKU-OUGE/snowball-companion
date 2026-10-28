"""Local, bounded private-state and loopback helpers for Snowball hooks."""
from contextlib import contextmanager
import fcntl, hashlib, json, os, stat, tempfile, time, unicodedata
from pathlib import Path
import urllib.request
HUB = 'http://127.0.0.1:4190'

def clean(value, chars=256, byte_limit=768):
    if not isinstance(value, str):
        return ''
    value = ' '.join(''.join(c for c in value if unicodedata.category(c)[0] != 'C' or c in '\n\t').split())
    result = value[:chars]
    while len(result.encode('utf-8')) > byte_limit:
        result = result[:-1]
    if result != value:
        result = result[:-1] + '…'
    return result


def opaque(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()[:32]


def root_dir():
    return Path.home() / '.config' / 'xueqiu'


def private_dir(path):
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = path.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise ValueError('untrusted_directory')


def private_open(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        os.close(fd)
        raise ValueError('untrusted_file')
    return fd


def read_token(root):
    with os.fdopen(private_open(root / 'hub-token', os.O_RDONLY)) as f:
        token = f.read(4097).strip()
    if not 32 <= len(token) <= 128 or any(not (c.isascii() and (c.isalnum() or c in '_-')) for c in token):
        raise ValueError('invalid_token')
    return token


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def request(path, token, data=None, timeout=.35):
    if path not in ('/v1/events', '/v1/health', '/v1/pet'):
        raise ValueError('unsupported_endpoint')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    req = urllib.request.Request(HUB + path, data=None if data is None else json.dumps(data, separators=(',', ':')).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'},
        method='GET' if data is None else 'POST')
    with opener.open(req, timeout=timeout) as response:
        raw = response.read(4097)
        if response.status != 200 or len(raw) > 4096:
            raise ValueError('invalid_response')
        return json.loads(raw)


@contextmanager
def locked(folder, sid):
    private_dir(folder.parent)
    private_dir(folder)
    fd = private_open(folder / (sid + '.lock'), os.O_RDWR | os.O_CREAT)
    try:
        until = time.monotonic() + .2
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > until:
                    raise TimeoutError('hook_busy')
                time.sleep(.01)
        yield
    finally:
        os.close(fd)


def save(path, state):
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, ensure_ascii=True, separators=(',', ':'))
            f.flush()
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
