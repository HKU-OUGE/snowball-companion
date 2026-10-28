"""Compile the actual native UTF-8 validator on host; no device or BLE access."""
import ctypes
from pathlib import Path
import subprocess
import tempfile
import unittest


class NativeMetadataTests(unittest.TestCase):
    def test_native_validator_unicode_and_malformed_bytes(self):
        source = Path(__file__).resolve().parent.parent / 'main/xueqiu_ble.c'
        text = source.read_text()
        function = text[text.index('static bool valid_display_text('):text.index('static bool valid_question(')]
        function = function.replace('static bool valid_title(', 'bool valid_title(', 1)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'validate.c').write_text('#include <stdbool.h>\n#include <stdint.h>\n#include <stddef.h>\n' + function)
            subprocess.run(['cc', '-shared', '-fPIC', '-Wall', '-Werror', str(root / 'validate.c'), '-o', str(root / 'validate.so')], check=True, capture_output=True)
            validate = ctypes.CDLL(str(root / 'validate.so')).valid_title
            validate.argtypes = (ctypes.c_char_p, ctypes.c_size_t)
            validate.restype = ctypes.c_bool
            for value in ['', '雪球界面 🐶', '汉' * 64, '🐶' * 48]:
                raw = value.encode()
                self.assertTrue(validate(raw, len(raw)), repr(value))
            for raw in [b'\0', b'\n', b'\xc0\x80', b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\xf0\x9f', b'\x80', ('汉' * 65).encode(), '\u202e'.encode()]:
                self.assertFalse(validate(raw, len(raw)), repr(raw))


if __name__ == '__main__':
    unittest.main()
