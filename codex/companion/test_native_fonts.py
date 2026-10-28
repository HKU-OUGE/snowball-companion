"""Actual external question/summary strings that previously produced tofu."""
from pathlib import Path
import re
import unittest

class TextCoverage(unittest.TestCase):
    def test_question_arrows_and_status_symbols_have_glyphs(self):
        source=(Path(__file__).parents[1]/'main/fonts/ui_font18b.c').read_text()
        glyphs={int(cp,16) for cp in re.findall(r'/\* U\+([0-9A-Fa-f]+)',source)}
        samples=[
            '上传百分比 → 电脑正在识别 → 文字结果',
            'CPU ≤ 160 MHz；电量 ≥ 20%；温度 25°C；α × β',
            '✅ 完成 ⚠️ 等待确认 ❌ 异常；🙂 收到啦',
            '建议使用“中文标点”、英文 apostrophe’s 和 A–B—C。',
        ]
        for sample in samples:
            with self.subTest(text=sample):
                self.assertEqual({c for c in sample if not c.isspace() and ord(c) not in glyphs},set())

    def test_common_chinese_coverage_is_retained(self):
        source=(Path(__file__).parents[1]/'main/fonts/ui_font18b.c').read_text()
        glyphs={int(cp,16) for cp in re.findall(r'/\* U\+([0-9A-Fa-f]+)',source)}
        han=set()
        for a in range(0xB0,0xF8):
            for b in range(0xA1,0xFF):
                try:han.add(ord(bytes((a,b)).decode('gb2312')))
                except UnicodeDecodeError:pass
        self.assertEqual(len(han),6763)
        self.assertFalse(han-glyphs)

    def test_extended_symbols_do_not_expand_or_escape_the_original_text_line(self):
        source=(Path(__file__).parents[1]/'main/fonts/ui_font18b.c').read_text()
        self.assertEqual(int(re.search(r'\.line_height\s*=\s*(\d+)',source)[1]),22)
        self.assertEqual(int(re.search(r'\.base_line\s*=\s*(\d+)',source)[1]),5)
        glyphs=re.findall(r'\.box_h = (\d+), \.ofs_x = -?\d+, \.ofs_y = (-?\d+)',source)
        self.assertGreater(len(glyphs),9000)
        self.assertEqual([(h,y) for h,y in glyphs if int(y)<-5 or int(y)+int(h)>17],[])

if __name__=='__main__':unittest.main()
