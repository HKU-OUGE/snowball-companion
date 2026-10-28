#!/usr/bin/env python3
"""Check the explicit legacy runtime entry and preserved standard manifest."""
import json
from pathlib import Path
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / 'plugins/xueqiu-status'
EXPECTED = {'SessionStart', 'UserPromptSubmit', 'PreToolUse', 'PermissionRequest',
            'PostToolUse', 'Stop', 'Interrupt', 'SessionEnd', 'PreCompact', 'PostCompact'}


class ManifestHookTests(unittest.TestCase):
    def test_legacy_runtime_entry_is_unambiguous(self):
        self.assertFalse((PLUGIN / 'plugin.json').exists(),
                         'This runtime ignores hooks when the standard root entry shadows legacy')
        legacy = json.loads((PLUGIN / '.codex-plugin/plugin.json').read_text())
        saved = json.loads((PLUGIN / 'plugin.agent-plugins.json').read_text())
        self.assertEqual(legacy['name'], 'xueqiu-status')
        self.assertEqual(legacy['version'], saved['version'])
        self.assertEqual(legacy['name'], saved['name'])
        self.assertEqual(legacy['hooks'], './hooks/hooks.json')
        self.assertEqual(saved['extensions']['com.openai']['hooks'], legacy['hooks'])
        self.assertEqual(legacy['skills'], './skills/')
        self.assertTrue((PLUGIN / 'skills/setup/SKILL.md').is_file())

    def test_all_ten_original_hook_definitions_and_script_are_preserved(self):
        hooks = json.loads((PLUGIN / 'hooks/hooks.json').read_text())['hooks']
        self.assertEqual(set(hooks), EXPECTED)
        for name, groups in hooks.items():
            with self.subTest(event=name):
                self.assertEqual(len(groups), 1)
                handlers = groups[0]['hooks']
                self.assertEqual(len(handlers), 1)
                self.assertEqual(handlers[0], {
                    'type': 'command',
                    'command': 'python3 "${PLUGIN_ROOT}/scripts/codex_hook.py" hook',
                    'timeout': 2})
        self.assertTrue((PLUGIN / 'scripts/codex_hook.py').is_file())


if __name__ == '__main__':
    unittest.main()
