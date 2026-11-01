"""Synthetic-only storage tests; includes real DPAPI tests on native Windows."""
import base64
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pairing_store as store
import platform_support as platform

CONFIG = {'v': 1, 'device': 'Xueqiu-01020304', 'key': 'ab' * 32}


def fake_dpapi(data, decrypt=False):
    # A test double, deliberately NOT cryptography. Native Windows tests below
    # exercise actual CryptProtectData and ACLs separately.
    return base64.b64decode(data[5:]) if decrypt else b'TEST:' + base64.b64encode(data)


class PairingStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'private/pairing.json'

    def test_private_roundtrip_and_no_additional_fields(self):
        store.save(CONFIG, self.path)
        self.assertEqual(store.load(self.path), CONFIG)
        self.assertTrue(platform.private_file_ok(self.path))
        before = self.path.read_bytes()
        with self.assertRaises(store.PairingStoreError):
            store.save({**CONFIG, 'other': 'ignored?'}, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_atomic_replace_failure_preserves_old(self):
        store.save(CONFIG, self.path)
        before = self.path.read_bytes()
        with patch.object(store.os, 'replace', side_effect=OSError('sensitive synthetic detail')):
            with self.assertRaisesRegex(store.PairingStoreError, '^credential_write_failed$'):
                store.save({**CONFIG, 'key': 'cd' * 32}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.pairing.json-*')), [])

    def test_invalid_shapes_rejected(self):
        for config in ({**CONFIG, 'v': True}, {**CONFIG, 'v': 2},
                       {**CONFIG, 'device': 'Other-01020304'}, {**CONFIG, 'key': 'aa'},
                       {**CONFIG, 'device': 'Xueqiu-123456789'}, {**CONFIG, 'key': None}):
            with self.subTest(config={k: v for k, v in config.items() if k != 'key'}):
                with self.assertRaises(store.PairingStoreError):
                    store.save(config, self.path)
        self.assertFalse(self.path.exists())

    def test_missing_and_duplicate_json_safe(self):
        with self.assertRaisesRegex(store.PairingStoreError, 'missing_credentials'):
            store.load(self.path)
        with self.assertRaisesRegex(store.PairingStoreError, '^invalid_credentials$'):
            store._json('{"key":"SECRET_SYNTHETIC", "key":"second"}')

    @unittest.skipIf(os.name == 'nt', 'POSIX symlink policy; Windows junction coverage is in platform tests')
    def test_symlink_and_hardlink_rejected(self):
        store.save(CONFIG, self.path)
        alias = self.path.with_name('alias')
        alias.symlink_to(self.path)
        with self.assertRaises(store.PairingStoreError):
            store.load(alias)
        alias.unlink()
        os.link(self.path, alias)
        with self.assertRaises(store.PairingStoreError):
            store.save(CONFIG, self.path)

    def test_windows_envelope_migration_once(self):
        # Explicit plaintext fixture even when executed on Windows.
        platform.ensure_private_dir(self.path.parent)
        self.path.write_text(json.dumps(CONFIG))
        platform.protect_private(self.path)
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi', side_effect=fake_dpapi) as crypto:
            self.assertEqual(store.load(self.path), CONFIG)
            envelope = json.loads(self.path.read_bytes())
            self.assertEqual(envelope['format'], store.FORMAT)
            self.assertNotIn(CONFIG['key'], self.path.read_text())
            writes = sum(not call.kwargs.get('decrypt', call.args[1] if len(call.args) > 1 else False) for call in crypto.call_args_list)
            self.assertEqual(writes, 1)
            before = self.path.read_bytes()
            self.assertEqual(store.load(self.path), CONFIG)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(sum(len(call.args) == 1 for call in crypto.call_args_list), 1)

    def test_migration_crypto_failure_preserves_legacy(self):
        platform.ensure_private_dir(self.path.parent)
        self.path.write_text(json.dumps(CONFIG))
        platform.protect_private(self.path)
        before = self.path.read_bytes()
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi', side_effect=store.PairingStoreError('dpapi_encrypt_failed')):
            with self.assertRaisesRegex(store.PairingStoreError, '^dpapi_encrypt_failed$'):
                store.load(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_migration_readback_failure_preserves_legacy(self):
        platform.ensure_private_dir(self.path.parent)
        self.path.write_text(json.dumps(CONFIG))
        platform.protect_private(self.path)
        before = self.path.read_bytes()
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi', side_effect=[b'encrypted', b'wrong']):
            with self.assertRaisesRegex(store.PairingStoreError, '^dpapi_verification_failed$'):
                store.load(self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_acl_failure_preserves_previous_config(self):
        store.save(CONFIG, self.path)
        before = self.path.read_bytes()
        original = platform.protect_private
        def reject_temporary(path):
            if Path(path).name.startswith('.pairing.json-'):
                raise OSError('synthetic ACL failure')
            return original(path)
        with patch.object(platform, 'protect_private', side_effect=reject_temporary):
            with self.assertRaisesRegex(store.PairingStoreError, '^credential_write_failed$'):
                store.save({**CONFIG, 'key': 'cd' * 32}, self.path)
        self.assertEqual(self.path.read_bytes(), before)

    def test_foreign_encrypted_inner_domain_rejected(self):
        envelope = {'format': store.FORMAT, 'version': 1, 'scope': 'current-user', 'blob': 'AA=='}
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi', return_value=json.dumps({'format': 'another-app', 'config': CONFIG}).encode()):
            with self.assertRaisesRegex(store.PairingStoreError, '^invalid_protected_credentials$'):
                store._decode(envelope)

    def test_foreign_dpapi_envelope_never_attempts_decryption(self):
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi') as crypto:
            with self.assertRaises(store.PairingStoreError):
                store._decode({'format': 'another-app', 'version': 1, 'scope': 'current-user', 'blob': 'AA=='})
            crypto.assert_not_called()

    def test_doctor_load_does_not_migrate(self):
        platform.ensure_private_dir(self.path.parent)
        self.path.write_text(json.dumps(CONFIG))
        platform.protect_private(self.path)
        before = self.path.read_bytes()
        with patch.object(store, 'WINDOWS', True), patch.object(store, '_dpapi') as crypto:
            self.assertEqual(store.load(self.path, migrate=False), CONFIG)
            crypto.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    @unittest.skipUnless(os.name == 'nt', 'Real user DPAPI and Windows DACL require Windows CI')
    def test_native_windows_dpapi_acl_and_tamper(self):
        store.save(CONFIG, self.path)
        self.assertTrue(platform.private_file_ok(self.path))
        self.assertEqual(store.load(self.path), CONFIG)
        self.assertNotIn(CONFIG['key'], self.path.read_text())
        envelope = json.loads(self.path.read_text())
        encrypted = bytearray(base64.b64decode(envelope['blob']))
        encrypted[-1] ^= 0x80
        envelope['blob'] = base64.b64encode(encrypted).decode()
        self.path.write_text(json.dumps(envelope))
        with self.assertRaisesRegex(store.PairingStoreError, '^dpapi_decrypt_failed$'):
            store.load(self.path)


if __name__ == '__main__':
    unittest.main()
