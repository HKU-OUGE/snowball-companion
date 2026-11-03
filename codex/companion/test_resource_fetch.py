"""Resource order, corruption, resume and progress tests; no live installation."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import resource_fetch as rf


class Response(io.BytesIO):
    status = 200
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status, self.headers = status, headers or {}


class Fetch(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="Snowball 中文 O'Brien ")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.card = self.root/'card'
        self.card.mkdir()
        self.progress = []
        self.fetcher = rf.ResourceFetcher([self.card], self.root/'cache', self.progress.append)
        self.data = b'fixture-data'
        self.entry = {'bytes':len(self.data), 'sha256':hashlib.sha256(self.data).hexdigest()}
        self.relative = Path('Snowball-Setup/payload.bin')
        self.source = self.card/self.relative
        self.source.parent.mkdir()

    def get(self, **kwargs):
        return self.fetcher.get('payload.bin', self.entry, self.relative, 'https://files.example/payload', **kwargs)

    def test_small_sd_file_wins_without_network(self):
        self.source.write_bytes(self.data)
        with patch.object(rf, 'open_https') as network:
            result = self.get()
        network.assert_not_called()
        self.assertEqual(result.read_bytes(),self.data)
        self.assertEqual(self.progress,['resources_sd'])

    def test_plugin_package_keeps_sd_priority_even_above_small_threshold(self):
        self.source.write_bytes(self.data)
        with patch.object(rf,'SMALL_BYTES',1),patch.object(rf,'open_https') as network:
            self.get(kind='plugin')
        network.assert_not_called()

    def test_verified_pc_cache_never_reads_card_or_network_again(self):
        self.source.write_bytes(self.data)
        result = self.get()
        self.source.unlink()
        with patch.object(self.fetcher,'from_sd') as sd,patch.object(rf,'open_https') as network:
            self.assertEqual(self.get(),result)
        sd.assert_not_called();network.assert_not_called()
        self.assertEqual(self.progress[-1],'resources_cache')

    def test_large_file_uses_network_before_present_sd(self):
        self.source.write_bytes(self.data)
        with patch.object(rf,'SMALL_BYTES',1),patch.object(rf,'open_https',return_value=Response(self.data)),patch.object(self.fetcher,'from_sd') as sd:
            result=self.get()
        sd.assert_not_called()
        self.assertEqual(result.read_bytes(),self.data)
        self.assertEqual(self.progress,['resources_network'])

    def test_network_unavailable_falls_back_and_does_not_retry_every_file(self):
        self.source.write_bytes(self.data)
        with patch.object(rf,'SMALL_BYTES',1),patch.object(rf,'open_https',side_effect=TimeoutError) as network:
            self.assertEqual(self.get().read_bytes(),self.data)
            other=self.fetcher.get('second.bin',self.entry,self.relative,'https://files.example/second')
        self.assertEqual(other.read_bytes(),self.data)
        self.assertEqual(network.call_count,1)
        self.assertEqual(self.progress,['resources_sd_fallback'])

    def test_corrupt_small_sd_file_falls_back_to_verified_network(self):
        self.source.write_bytes(b'x'*len(self.data))
        with patch.object(rf,'open_https',return_value=Response(self.data)):
            self.assertEqual(self.get().read_bytes(),self.data)

    def test_corrupt_network_is_never_published_and_can_use_pinned_sd(self):
        self.source.write_bytes(self.data)
        with patch.object(rf,'SMALL_BYTES',1),patch.object(rf,'open_https',return_value=Response(b'x'*len(self.data))):
            result=self.get()
        self.assertEqual(result.read_bytes(),self.data)
        self.assertFalse(result.with_name(result.name+'.part').exists())

    def test_no_valid_source_raises_without_publishing_bad_file(self):
        with patch.object(rf,'open_https',return_value=Response(b'x'*len(self.data))):
            with self.assertRaises(rf.ResourceUnavailable):self.get()
        self.assertFalse(any(p.name=='payload.bin' for p in (self.root/'cache').rglob('*')))

    def test_partial_download_resumes_exact_range_after_retry(self):
        class Interrupted(Response):
            def read1(self, count):
                if self.tell():raise TimeoutError
                return super().read1(4)
        with patch.object(rf,'SMALL_BYTES',1),patch.object(rf,'open_https',return_value=Interrupted(self.data)):
            with self.assertRaises(rf.ResourceUnavailable):self.get()
        partial=self.root/'cache'/self.entry['sha256']/'payload.bin.part'
        self.assertEqual(partial.read_bytes(),self.data[:4])
        self.fetcher=rf.ResourceFetcher([self.card],self.root/'cache')
        def serve(request):
            self.assertEqual(request.get_header('Range'),'bytes=4-')
            return Response(self.data[4:],206,{'Content-Range':f'bytes 4-{len(self.data)-1}/{len(self.data)}'})
        with patch.object(rf,'open_https',side_effect=serve):self.assertEqual(self.get().read_bytes(),self.data)
        self.assertFalse(partial.exists())

    def test_ignored_range_restarts_without_duplicate_bytes(self):
        directory=rf.private_dir(self.root/'cache'/self.entry['sha256'])
        (directory/'payload.bin.part').write_bytes(self.data[:4])
        with patch.object(rf,'open_https',return_value=Response(self.data)):
            self.assertEqual(self.get().read_bytes(),self.data)

    def test_incorrect_content_range_never_appends(self):
        directory=rf.private_dir(self.root/'cache'/self.entry['sha256'])
        partial=directory/'payload.bin.part';partial.write_bytes(self.data[:4])
        with patch.object(rf,'open_https',return_value=Response(self.data[4:],206,{'Content-Range':'bytes 0-7/12'})):
            with self.assertRaises(rf.ResourceUnavailable):self.get()
        self.assertEqual(partial.read_bytes(),self.data[:4])

    def test_long_advancing_transfer_has_no_total_time_budget(self):
        # Every call makes progress; wall-clock age cannot cancel this loop.
        class Slow(Response):
            def read1(self,count):return super().read1(1)
        with patch.object(rf,'open_https',return_value=Slow(self.data)),patch('time.monotonic',side_effect=[0,100000]):
            self.assertEqual(self.get().read_bytes(),self.data)

    def test_url_path_and_entry_guards(self):
        for url in ('http://example/a','file:///tmp/example','https://user:pass@example/a'):
            with self.assertRaises(rf.ResourceUnavailable):rf.https_url(url)
        for name in ('../file','..','dir/file','dir\\file'):
            with self.assertRaises(ValueError):self.fetcher.get(name,self.entry,self.relative)
        with self.assertRaises(ValueError):self.fetcher.get('ok',self.entry,'../outside')

    def test_pypi_lookup_requires_pinned_filename_size_and_hash(self):
        from urllib.error import URLError
        filename='example-1.0-py3-none-any.whl'
        item={'filename':filename,'size':self.entry['bytes'],'digests':{'sha256':self.entry['sha256']},'url':'https://files.pythonhosted.org/pinned.whl'}
        with patch.object(rf,'open_https',return_value=Response(json.dumps({'urls':[item]}).encode())):
            self.assertEqual(rf.wheel_url(filename,self.entry),item['url'])
        item['digests']['sha256']='0'*64
        with patch.object(rf,'open_https',return_value=Response(json.dumps({'urls':[item]}).encode())):
            with self.assertRaises(rf.ResourceUnavailable):rf.wheel_url(filename,self.entry)
        with patch.object(rf,'open_https',side_effect=URLError('offline')):
            with self.assertRaises(rf.ResourceUnavailable):rf.wheel_url(filename,self.entry)


if __name__=='__main__':unittest.main()
