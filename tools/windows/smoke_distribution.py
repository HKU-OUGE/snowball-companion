#!/usr/bin/env python3
"""Exercise packaged entry points with a temporary home; no device or service."""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
import struct
import subprocess
import tempfile
from unittest.mock import patch
from stage_payload import verify
from distribution_manifest import verify as verify_distribution


def pe_subsystem(path):
    data = Path(path).read_bytes()
    if data[:2] != b'MZ':
        raise ValueError('Expected a Windows executable')
    pe = struct.unpack_from('<I', data, 0x3c)[0]
    if data[pe:pe+4] != b'PE\0\0' or struct.unpack_from('<H', data, pe+4)[0] != 0x8664:
        raise ValueError('Expected Windows x64 PE')
    if struct.unpack_from('<H', data, pe+24)[0] != 0x20b:
        raise ValueError('Expected PE32+ executable')
    return struct.unpack_from('<H', data, pe+24+68)[0]


def run_distribution(directory):
    directory = Path(directory).resolve()
    cli, gui = directory/'SnowballCLI.exe', directory/'Snowball.exe'
    if pe_subsystem(cli) != 3 or pe_subsystem(gui) != 2:
        raise ValueError('CLI/GUI subsystem mismatch')
    verify_distribution(directory)
    bundle = directory/'_internal/bundled'
    verify(bundle)
    with tempfile.TemporaryDirectory(prefix='snowball-exe-fixture-') as temporary:
        home = Path(temporary)/'合成 用户 profile'
        home.mkdir()
        env = dict(os.environ, USERPROFILE=str(home), HOME=str(home),
                   APPDATA=str(home/'应用 数据/漫游'), LOCALAPPDATA=str(home/'应用 数据/本地'),
                   CODEX_HOME=str(home/'.codex'), XDG_STATE_HOME=str(home/'.local/state'),
                   XDG_CONFIG_HOME=str(home/'.config'), PYTHONUTF8='1')
        def run(exe, *args, payload=None):
            result = subprocess.run([str(exe), *map(str,args)], input=payload,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
                env=env, cwd=home, timeout=40)
            if result.returncode:
                # Only synthetic fixture output; no real profile was provided.
                raise RuntimeError(f'Packaged {args[0] if args else "entry"} failed: {result.stderr}')
            return result
        version = run(cli, '--version').stdout.strip()
        if version != 'Snowball Companion 0.6.0':
            raise ValueError('Unexpected Windows release version')
        report = json.loads(run(cli, 'doctor', '--json').stdout)
        if not isinstance(report, dict):
            raise ValueError('Doctor must return structured diagnostics')
        dependencies = next((row for row in report.get('checks', []) if row.get('code') == 'dependencies'), {})
        if dependencies.get('versions') != {'bleak': '3.0.2', 'pyserial': '3.5'}:
            raise ValueError('Frozen dependency imports or package metadata are missing')
        run(gui, 'gui', '--smoke-test')
        script = bundle/'codex/plugins/xueqiu-status/scripts/codex_hook.py'
        session = 'windows-ci-合成会话'
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop'):
            payload = json.dumps({'hook_event_name': event, 'session_id': session,
                                  'prompt': '合成测试，不是真实会话'}, ensure_ascii=False)
            result = run(cli, 'hook', '--script', script, payload=payload)
            if json.loads(result.stdout) != {}:
                raise ValueError('Hook emitted an unexpected control decision')
        record_path = home/'.local/state/xueqiu/codex-adapter/hooks'/(
            hashlib.sha256(session.encode()).hexdigest()[:32] + '.json')
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if record.get('seq') != 3:
            raise ValueError('Frozen hook did not preserve all synthetic events')
        # Exercise the real installer's atomic copy only, never shortcut,
        # plugin registration, supervisor start or hardware provisioning.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'codex/companion'))
        with patch.dict(os.environ, env, clear=True):
            from windows_install import install_bundle
            installed = install_bundle(directory)
        if not installed['directory'].is_relative_to(Path(env['LOCALAPPDATA'])):
            raise ValueError('Installer escaped isolated AppData')
        verify_distribution(installed['directory'])
        if run(installed['cli'], '--version').stdout.strip() != version:
            raise ValueError('Installed CLI cannot start')
        cache = Path(env['CODEX_HOME'])/'plugins/cache/xueqiu-local/xueqiu-status/0.6.0'
        shutil.copytree(installed['directory']/'_internal/bundled/codex/plugins/xueqiu-status', cache)
        env['PLUGIN_ROOT'] = str(cache)
        system = Path(env.get('SYSTEMROOT') or env['SystemRoot'])/'System32'
        powershell = system/'WindowsPowerShell/v1.0/powershell.exe'
        env['PATH'] = str(system) + os.pathsep + str(powershell.parent)
        hooks = json.loads((cache/'hooks/hooks.json').read_text(encoding='utf-8'))['hooks']
        expected_script = (cache/'scripts/windows_hook.ps1').read_text(encoding='utf-8')
        for event in ('SessionStart', 'UserPromptSubmit', 'Stop'):
            command = hooks[event][0]['hooks'][0]['commandWindows']
            match = re.fullmatch(r'powershell\.exe -NoProfile -NonInteractive -EncodedCommand ([A-Za-z0-9+/=]+)', command)
            if not match or base64.b64decode(match[1], validate=True).decode('utf-16-le') != expected_script:
                raise ValueError('commandWindows differs from reviewed launcher')
            payload = json.dumps({'hook_event_name': event, 'session_id': session}, ensure_ascii=False)
            result = run(powershell, '-NoProfile', '-NonInteractive', '-EncodedCommand', match[1], payload=payload)
            if json.loads(result.stdout) != {}:
                raise ValueError('Installed PowerShell hook did not acknowledge fixture')
        record = json.loads(record_path.read_text(encoding='utf-8'))
        if record.get('seq') != 6:
            raise ValueError('Installed commandWindows lost UTF-8 synthetic events')
        if list((home/'.config').rglob('ble-pairing.json')) or list((home/'.config').rglob('hub-token')):
            raise ValueError('Smoke test unexpectedly created credentials')
    return {'version': version, 'doctor': 'ok', 'gui_smoke': 'ok', 'hook_events': 6, 'isolated_install': 'ok', 'commandWindows': 'ok'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run EXE smoke checks on native Windows')
    print(json.dumps(run_distribution(args.directory), sort_keys=True))


if __name__ == '__main__': main()
