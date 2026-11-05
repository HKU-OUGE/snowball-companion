import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('installer_bootstrap',Path(__file__).with_name('bootstrap.py'))
bootstrap=importlib.util.module_from_spec(spec);spec.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.source=self.root/'source';self.source.mkdir()
        python=bootstrap.python_at(self.source);python.parent.mkdir(parents=True);python.touch()
        (self.source/'app.txt').write_bytes(b'reviewed application')
        self.manifest={'product':'snowball-companion','version':bootstrap.VERSION,
            'application_sha256':{'app.txt':hashlib.sha256(b'reviewed application').hexdigest()}}
        self.save()

    def save(self):
        (self.source/'INSTALLER.json').write_text(json.dumps(self.manifest))

    def test_installs_versioned_copy_and_reuses_identical_install(self):
        dest=bootstrap.stage(self.source,self.root/'installed')
        self.assertEqual(bootstrap.stage(self.source,self.root/'installed'),dest)
        self.assertEqual((dest/'app.txt').read_bytes(),b'reviewed application')

    def test_tampering_refused_before_installation(self):
        (self.source/'app.txt').write_bytes(b'modified')
        with self.assertRaises(RuntimeError):bootstrap.stage(self.source,self.root/'installed')
        self.assertFalse((self.root/'installed').exists())

    def test_changed_existing_version_preserved(self):
        dest=bootstrap.stage(self.source,self.root/'installed');(dest/'app.txt').write_text('keep me')
        with self.assertRaises(RuntimeError):bootstrap.stage(self.source,self.root/'installed')
        self.assertEqual((dest/'app.txt').read_text(),'keep me')

    def test_path_traversal_refused(self):
        self.manifest['application_sha256']['../outside']='bad';self.save()
        with self.assertRaises(RuntimeError):bootstrap.verify(self.source)


if __name__=='__main__':unittest.main()
