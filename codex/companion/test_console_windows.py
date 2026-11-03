"""Native Windows entry/shortcut/firewall-plan tests; no services or firewall changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'win32', 'Requires native Windows and PowerShell')
class NativeWindowsConsole(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Snowball O'Brien 中文 ")
        self.addCleanup(self.temp.cleanup)
        # Native tools may expand the runner's RUNNER~1 short home alias.
        self.root = Path(self.temp.name).resolve()

    def powershell(self, script, *args):
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), *map(str, args)],
                                capture_output=True, text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONUTF8='1'))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout.strip()

    def test_every_shipped_console_powershell_script_parses_natively(self):
        parser = self.root / 'parse.ps1'
        parser.write_text("param([string]$Source)\n$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($Source,[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){$errors|ForEach-Object{Write-Error $_};exit 1};Write-Output 'parsed'\n", encoding='utf-8')
        for script in ('Open-Snowball.ps1', 'install-windows.ps1', 'tools/install_console_shortcut.ps1',
                       'companion/windows-services.ps1', 'companion/windows-lan-access.ps1', 'companion/windows-login-info.ps1', 'companion/windows-console-control.ps1'):
            with self.subTest(script=script):
                self.assertEqual(self.powershell(parser, '-Source', ROOT / script), 'parsed')

    def test_login_inspection_plan_is_read_only_and_user_scoped(self):
        value=json.loads(self.powershell(ROOT/'companion/windows-login-info.ps1','-Plan'))
        self.assertTrue(value['userOnly'])
        self.assertFalse(value['mutation'])

    def test_console_control_plan_is_exact_and_user_scoped(self):
        value=json.loads(self.powershell(ROOT/'companion/windows-console-control.ps1','-Runtime',self.root,'-Plan'))
        self.assertTrue(value['currentUserOnly'])
        self.assertTrue(value['exactlyOne'])
        self.assertFalse(value['stopRequested'])

    def fake_entry(self):
        (self.root / 'companion').mkdir()
        (self.root / 'companion/console_launcher.py').write_text('import sys,json;print(json.dumps(sys.argv))\n', encoding='utf-8')
        shutil.copy2(ROOT / 'Open-Snowball.ps1', self.root / 'Open-Snowball.ps1')
        shutil.copy2(ROOT / 'Open-Snowball.cmd', self.root / 'Open-Snowball.cmd')

    def test_powershell_entry_preserves_spaces_unicode_and_apostrophe(self):
        self.fake_entry()
        value = json.loads(self.powershell(self.root / 'Open-Snowball.ps1', '-Python', sys.executable, '-NoBrowser'))
        self.assertTrue(os.path.samefile(value[0], self.root / 'companion/console_launcher.py'))
        self.assertEqual(value[1:], ['open', '--no-browser'])

    def test_cmd_double_click_path_invokes_bundled_launcher(self):
        self.fake_entry()
        # A fixed command name in a Unicode cwd tests the actual %~dp0 quoting,
        # without interpolating a shell path or opening a real browser.
        result = subprocess.run(['cmd.exe', '/d', '/c', 'Open-Snowball.cmd'], cwd=self.root,
                                capture_output=True, text=True, encoding='utf-8', timeout=30,
                                env=dict(os.environ, PYTHONUTF8='1', PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH']))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        args = json.loads(result.stdout.strip())
        self.assertTrue(os.path.samefile(args[0], self.root / 'companion/console_launcher.py'))
        self.assertEqual(args[1:], ['open'])

    def test_desktop_shortcut_round_trip_without_touching_real_desktop(self):
        self.fake_entry()
        tools = self.root / 'tools'
        tools.mkdir()
        shutil.copy2(ROOT / 'tools/install_console_shortcut.ps1', tools / 'install_console_shortcut.ps1')
        desktop = self.root / 'isolated desktop'
        self.powershell(tools / 'install_console_shortcut.ps1', '-Python', sys.executable, '-Destination', desktop)
        links = list(desktop.glob('*.lnk'))
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].name, '雪球控制台.lnk')
        value = json.loads(self.powershell(tools / 'install_console_shortcut.ps1', '-Inspect', links[0]))
        self.assertTrue(value['target'].lower().endswith('powershell.exe'))
        self.assertIn('"' + str(self.root / 'Open-Snowball.ps1') + '"', value['arguments'])
        self.assertIn('"' + sys.executable + '"', value['arguments'])
        self.assertTrue(os.path.samefile(value['cwd'], self.root))
        self.assertEqual(value['description'], 'Snowball Companion local console')

    def test_firewall_plan_is_program_private_subnet_and_single_port_only(self):
        value = json.loads(self.powershell(ROOT / 'companion/windows-lan-access.ps1', '-Python', sys.executable, '-Plan'))
        self.assertEqual(value['LocalPort'], 4193)
        self.assertEqual(value['Profile'], 'Private')
        self.assertEqual(value['RemoteAddress'], 'LocalSubnet')
        self.assertEqual(value['Protocol'], 'TCP')
        self.assertEqual(Path(value['Program']), Path(sys.executable))


if __name__ == '__main__':
    unittest.main()
