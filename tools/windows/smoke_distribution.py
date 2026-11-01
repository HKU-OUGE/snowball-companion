#!/usr/bin/env python3
"""Exercise packaged entry points with a temporary home; isolated CI services, no device pairing."""
import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import shutil
import sys
from pathlib import Path
import struct
import subprocess
import tempfile
import time
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


def runtime_session_id():
    """Read the actual Windows session; never impersonate an interactive user."""
    if os.name != 'nt':
        raise RuntimeError('Real runtime smoke requires Windows')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.ProcessIdToSessionId.restype = wintypes.BOOL
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        raise ctypes.WinError(ctypes.get_last_error())
    return session.value


def read_private_json(path):
    from platform_support import private_file_ok, open_nofollow
    if not private_file_ok(path):
        raise ValueError('CI runtime record failed its real privacy check')
    with os.fdopen(open_nofollow(path, os.O_RDONLY), 'r', encoding='utf-8') as source:
        return json.loads(source.read(8193))


def reject_unsigned_control(port):
    """A bad HMAC must not execute an otherwise valid status operation."""
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
    try:
        body = json.dumps({'op': 'status', 'nonce': '0'*32}, separators=(',', ':'))
        connection.request('POST', '/control', body=body,
            headers={'Content-Type': 'application/json', 'X-Snowball-Proof': '0'*64})
        response = connection.getresponse()
        response.read(8192)
        if response.status != 403:
            raise ValueError('Supervisor accepted an invalid control HMAC')
    finally:
        connection.close()


def verify_hub_health(home):
    from platform_support import private_file_ok, open_nofollow
    path = home/'.config/xueqiu/hub-token'
    if not private_file_ok(path):
        raise ValueError('CI Hub token failed its real privacy check')
    with os.fdopen(open_nofollow(path, os.O_RDONLY), 'r', encoding='utf-8') as source:
        token = source.read(130).strip()
    connection = http.client.HTTPConnection('127.0.0.1', 4190, timeout=3)
    try:
        connection.request('GET', '/v1/health', headers={'Authorization': 'Bearer '+token})
        response = connection.getresponse()
        value = json.loads(response.read(1024))
        if response.status != 200 or value.get('ok') is not True or value.get('hub_alive') is not True:
            raise ValueError('Real isolated Hub health check failed')
    finally:
        connection.close()


def exercise_runtime(cli, home, run):
    """Real installed CLI lifecycle, within the CI account's temporary profile.

    No credential fixture is installed: the bridge must exit at missing_credentials
    before any BLE scan/connect. No task is deleted unless the product proves
    ownership for this exact user and installation. All attempts have cleanup.
    """
    if runtime_session_id() == 0:
        print('::warning::Runtime/autostart smoke skipped: Session 0 is noninteractive; product guard retained')
        return {'state': 'skipped', 'reason': 'session_0_noninteractive',
                'supervisor': 'not_tested', 'autostart': 'not_tested'}
    def command(operation):
        value = json.loads(run(cli, 'runtime', operation).stdout)
        if not isinstance(value, dict):
            raise ValueError('Invalid runtime CLI response')
        return value
    before = command('status')
    if before.get('state') != 'stopped' or before.get('running') is not False:
        raise ValueError('CI runtime profile is not fresh; refusing to stop an existing process')
    task_before = command('autostart-status')
    if task_before.get('state') != 'absent':
        raise ValueError('CI user already has an autostart task; refusing to replace it')
    state_dir = home/'.local/state/xueqiu'
    record_path = state_dir/'windows-runtime/runtime.json'
    diagnostic_path = state_dir/'transport-diagnostic.json'
    attempted_start = attempted_task = False
    try:
        attempted_start = True  # Also clean up a partially successful launch.
        started = command('start')
        if started.get('running') is not True:
            raise ValueError('Supervisor start was not acknowledged')
        # run() uses subprocess.run: the launcher CLI has already exited here.
        deadline = time.monotonic()+25
        while True:
            snapshot = command('status')  # Product verifies both request and response HMAC.
            hub = snapshot.get('children', {}).get('hub', {})
            try:
                diagnostic = read_private_json(diagnostic_path)
            except (OSError, ValueError):
                diagnostic = {}
            if (snapshot.get('running') is True and hub.get('healthy') is True and
                    type(hub.get('pid')) is int and hub['pid'] > 0 and
                    diagnostic.get('state') == 'error' and diagnostic.get('error') == 'missing_credentials'):
                break
            if time.monotonic() >= deadline:
                raise ValueError('Real Hub health or missing-credentials bridge diagnostic did not become ready')
            time.sleep(.2)
        first = read_private_json(record_path)
        reject_unsigned_control(first['port'])
        verify_hub_health(home)
        time.sleep(.3)
        repeated = command('start')
        again = read_private_json(record_path)
        if (repeated.get('running') is not True or first['identity'] != again['identity'] or
                first['port'] != again['port'] or first['install'] != again['install'] or
                repeated.get('children', {}).get('hub', {}).get('pid') != hub['pid']):
            raise ValueError('Repeated start created a second supervisor or Hub')
        attempted_task = True
        created = command('install-autostart')
        if created.get('installed') is not True or created.get('interactive') is not True or created.get('elevated') is not False:
            raise ValueError('Interactive least-privilege autostart was not installed')
        task = command('autostart-status')
        if task.get('state') != 'owned' or task.get('owned') is not True or task.get('task') != created.get('task'):
            raise ValueError('Installed autostart task ownership was not verified')
    finally:
        cleanup_errors = []
        if attempted_task:
            try:
                command('remove-autostart')
                if command('autostart-status').get('state') != 'absent':
                    raise ValueError('Owned autostart task was not removed')
            except Exception:
                cleanup_errors.append('autostart cleanup failed')
        if attempted_start:
            try:
                stopped = command('stop')
                if stopped.get('running') is not False or stopped.get('state') != 'stopped':
                    raise ValueError('Supervisor stop was not confirmed')
                if command('status').get('state') != 'stopped' or record_path.exists():
                    raise ValueError('Supervisor state persisted after shutdown')
            except Exception:
                cleanup_errors.append('runtime cleanup failed')
        if cleanup_errors:
            raise RuntimeError('; '.join(cleanup_errors))
    if (home/'.config/xueqiu/ble-pairing.json').exists():
        raise ValueError('Runtime smoke unexpectedly created Bluetooth credentials')
    return {'state': 'verified', 'supervisor': 'survived_launcher_exit',
            'hub': 'authenticated_health', 'control_hmac': 'valid_accepted_invalid_rejected',
            'repeat_start': 'same_processes', 'bridge': 'missing_credentials',
            'autostart': 'owned_created_and_removed', 'stop': 'confirmed'}


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
                   XDG_CONFIG_HOME=str(home/'.config'), PYTHONUTF8='0')
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
        pets = json.loads(run(cli, 'pet', 'list').stdout)
        if not pets:
            raise ValueError('Bundled Pet helper did not read its UTF-8 catalogue')
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
        runtime = exercise_runtime(installed['cli'], home, run)
        verify_distribution(installed['directory'])
    return {'version': version, 'doctor': 'ok', 'gui_smoke': 'ok', 'hook_events': 6, 'isolated_install': 'ok', 'commandWindows': 'ok', 'runtime_lifecycle': runtime}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Run EXE smoke checks on native Windows')
    print(json.dumps(run_distribution(args.directory), sort_keys=True))


if __name__ == '__main__': main()
