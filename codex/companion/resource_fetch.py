"""Pinned resources: cache first, SD for small files, network for large files.

No credentials or arbitrary URLs come from the browser/SD. Callers supply the
bundled manifest; hashes are checked before any installer/model consumes a file.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.error import URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from private_files import checked_path, private_dir, private_file, safe_reader

SMALL_BYTES = 1024 * 1024
IDLE_TIMEOUT = 15
STAGES = frozenset(('resources_cache', 'resources_network', 'resources_sd', 'resources_sd_fallback'))


class ResourceUnavailable(Exception):
    """No usable source; caller must not install an unverified partial file."""


def valid(path, entry):
    try:
        if path.stat().st_size != entry['bytes']:
            return False
        with safe_reader(path) as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == entry['sha256']
    except OSError:
        return False


def https_url(url):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ResourceUnavailable('resource_url_invalid')
    return url


class HTTPSRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, https_url(newurl))


def open_https(request):
    https_url(request.full_url)
    return build_opener(HTTPSRedirect()).open(request, timeout=IDLE_TIMEOUT)


def wheel_url(name, entry):
    # Wheel distribution/version components cannot contain '-'. Match the
    # exact filename AND the bundled digest, never use a mutable latest release.
    package, version = name.split('-')[:2]
    url = 'https://pypi.org/pypi/' + quote(package, safe='') + '/' + quote(version, safe='') + '/json'
    try:
        with open_https(Request(url, headers={'Accept': 'application/json'})) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ResourceUnavailable('resource_index_invalid')
        data = json.loads(raw)
        for item in data.get('urls', []):
            if (item.get('filename') == name and item.get('digests', {}).get('sha256') == entry['sha256']
                    and item.get('size') == entry['bytes']):
                return https_url(item['url'])
    except (URLError, OSError, http.client.HTTPException, ValueError, KeyError, TypeError):
        raise ResourceUnavailable('resource_network_unavailable') from None
    raise ResourceUnavailable('resource_not_published')


class ResourceFetcher:
    def __init__(self, roots=None, cache=None, progress=None):
        self._roots = roots
        self.cache = private_dir(cache or Path.home() / '.cache/xueqiu/resources')
        self.progress = progress or (lambda stage: None)
        self.network_failed = False
        self.last_source = None

    def emit(self, source):
        if source == 'sd' and self.network_failed:
            source = 'sd_fallback'
        if source != self.last_source:
            self.progress('resources_' + source)
            self.last_source = source

    def roots(self):
        if self._roots is None:
            from speech_offline import mounted_roots
            self._roots = mounted_roots()
        return self._roots

    @staticmethod
    def validate(name, entry):
        if (not isinstance(name, str) or not name or '/' in name or '\\' in name or name in ('.', '..')
                or not isinstance(entry.get('bytes'), int) or not 0 < entry['bytes'] < 4 * 1024**3
                or not re.fullmatch('[a-f0-9]{64}', entry.get('sha256', ''))):
            raise ValueError('invalid bundled resource')

    def from_sd(self, relative, target, entry):
        for root in self.roots():
            source = Path(root) / relative
            try:
                if source.stat().st_size != entry['bytes']:
                    continue
                with safe_reader(source) as stream:
                    self.emit('sd')
                    fd, name = tempfile.mkstemp(prefix='.sd-', dir=target.parent)
                    temp = Path(name)
                    try:
                        count = 0
                        digest = hashlib.sha256()
                        with os.fdopen(fd, 'wb') as output:
                            while True:
                                try:
                                    chunk = stream.read(128 * 1024)
                                except OSError:
                                    raise ResourceUnavailable('resource_sd_removed') from None
                                if not chunk:
                                    break
                                count += len(chunk)
                                if count > entry['bytes']:
                                    raise ResourceUnavailable('resource_sd_changed')
                                output.write(chunk)
                                digest.update(chunk)
                            output.flush()
                            os.fsync(output.fileno())
                        if count != entry['bytes'] or digest.hexdigest() != entry['sha256']:
                            continue
                        private_file(temp)
                        os.replace(temp, target)
                        checked_path(target.with_name(target.name + '.part')).unlink(missing_ok=True)
                        return target
                    finally:
                        temp.unlink(missing_ok=True)
            except (FileNotFoundError, NotADirectoryError, PermissionError, ResourceUnavailable):
                continue
        return None

    def download(self, url, target, entry):
        partial = checked_path(target.with_name(target.name + '.part'))
        if partial.exists() and partial.stat().st_size >= entry['bytes']:
            if valid(partial, entry):
                os.replace(partial, target)
                return target
            partial.unlink()
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {'Accept-Encoding': 'identity', 'User-Agent': 'Snowball-Companion-Setup'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        try:
            response = open_https(Request(https_url(url), headers=headers))
        except (URLError, OSError, http.client.HTTPException):
            raise ResourceUnavailable('resource_network_unavailable') from None
        with response:
            if response.status == 200:
                offset = 0  # Server ignored Range: truncate, never append twice.
            elif response.status == 206:
                expected = f'bytes {offset}-{entry["bytes"]-1}/{entry["bytes"]}'
                if response.headers.get('Content-Range') != expected:
                    raise ResourceUnavailable('resource_range_invalid')
            else:
                raise ResourceUnavailable('resource_response_invalid')
            self.emit('network')
            with partial.open('ab' if offset else 'wb') as output:
                private_file(partial)
                count = offset
                while True:
                    try:
                        # read1 returns available bytes. A long transfer with
                        # progress has no total timeout; 15s applies to a blocked
                        # socket read, not a slow but advancing download.
                        chunk = response.read1(64 * 1024)
                    except (OSError, http.client.HTTPException):
                        raise ResourceUnavailable('resource_network_stalled') from None
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > entry['bytes']:
                        raise ResourceUnavailable('resource_size_invalid')
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        if count != entry['bytes']:
            raise ResourceUnavailable('resource_download_incomplete')
        if not valid(partial, entry):
            partial.unlink()
            raise ResourceUnavailable('resource_hash_invalid')
        os.replace(partial, target)
        return target

    def get(self, name, entry, relative, url=None, *, kind='dependency'):
        self.validate(name, entry)
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('invalid resource path')
        target = checked_path(private_dir(self.cache / entry['sha256']) / name)
        if valid(target, entry):
            self.emit('cache')
            return target
        sd_first = kind in ('plugin', 'guide', 'metadata') or entry['bytes'] <= SMALL_BYTES
        if sd_first or self.network_failed:
            found = self.from_sd(relative, target, entry)
            if found:
                return found
        if url is not None and not self.network_failed:
            try:
                return self.download(url() if callable(url) else url, target, entry)
            except ResourceUnavailable:
                self.network_failed = True
                self.emit('sd_fallback')
        if not sd_first:
            found = self.from_sd(relative, target, entry)
            if found:
                return found
        raise ResourceUnavailable('resource_unavailable')
