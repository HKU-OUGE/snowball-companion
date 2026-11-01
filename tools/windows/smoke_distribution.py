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
import stat
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


class PackagedCommandError(RuntimeError):
    def __init__(self, arguments, result):
        self.arguments = tuple(map(str, arguments))
        self.returncode = result.returncode
        self.stderr = result.stderr
        super().__init__(f'Packaged {self.arguments[0] if self.arguments else "entry"} failed: {result.stderr}')


def verified_host_job_restriction(error):
    """Only the production CLI's exact, corroborated spawn rejection qualifies."""
    if (not isinstance(error, PackagedCommandError) or error.arguments != ('runtime', 'start')
            or error.returncode != 2):
        return False
    try:
        report = json.loads(error.stderr)
    except (TypeError, ValueError):
        return False
    if not isinstance(report, dict):
        return False
    required = {'ok': False, 'operation': 'start', 'code': 'host_job_restricted',
                'winerror': 5, 'in_job': True, 'breakaway_allowed': False,
                'silent_breakaway': False}
    if any(type(report.get(key)) is not type(value) or report[key] != value for key, value in required.items()):
        return False
    current = current_job_diagnostic()
    return current.get('available') is True and current.get('in_job') is True


def launch_foreground_in_ci(cli, home, env):
    """Retain a Popen handle; inherit the CI Job without breakaway flags."""
    from platform_support import ensure_private_dir, open_nofollow, protect_private
    directory = ensure_private_dir(home/'.local/state/xueqiu/windows-runtime')
    path = directory/'supervisor.log'
    fd = open_nofollow(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND)
    try:
        protect_private(path)
        with os.fdopen(fd, 'ab', buffering=0) as log:
            fd = None
            return subprocess.Popen([str(cli), 'runtime', 'run'], stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=home,
                close_fds=True, creationflags=0)
    finally:
        if fd is not None:
            os.close(fd)


def current_job_diagnostic():
    """Read-only process membership; does not alter jobs or bypass constraints."""
    if os.name != 'nt':
        return {'supported': False}
    import ctypes
    from ctypes import wintypes
    try:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.argtypes = []
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE,
                                         ctypes.POINTER(wintypes.BOOL)]
        kernel.IsProcessInJob.restype = wintypes.BOOL
        member = wintypes.BOOL()
        if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(member)):
            return {'supported': True, 'available': False, 'winerror': ctypes.get_last_error()}
        return {'supported': True, 'available': True, 'in_job': bool(member.value)}
    except OSError as error:
        return {'supported': True, 'available': False, 'winerror': getattr(error, 'winerror', None)}


def collect_runtime_failure(home):
    """Only this smoke's synthetic fixture logs, never runtime.json or tokens.

    This is deliberately not a general product log collector. Its only caller
    supplies the TemporaryDirectory profile created in run_distribution.
    """
    from platform_support import open_nofollow, reject_links
    directory = Path(home)/'.local/state/xueqiu/windows-runtime'
    result = {'scope': 'synthetic_windows_runtime_fixture',
              'current_process_job': current_job_diagnostic(), 'log_tails': {}}
    reject_links(directory)
    # The supervisor owns exactly these log files. Do not recursively inspect
    # state or follow a log-shaped link to a record/credential outside the list.
    for name in ('supervisor.log', 'hub.log', 'bridge.log'):
        path = directory/name
        try:
            info = path.lstat()
            reject_links(path)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            with os.fdopen(open_nofollow(path, os.O_RDONLY), 'rb') as source:
                source.seek(0, os.SEEK_END)
                source.seek(max(0, source.tell()-16384))
                tail = source.read(16384)
            result['log_tails'][name] = {'bytes_read': len(tail), 'tail': tail.decode('utf-8', errors='replace')}
        except FileNotFoundError:
            continue
        except OSError as error:
            result['log_tails'][name] = {'read_error': type(error).__name__,
                                        'winerror': getattr(error, 'winerror', None)}
    return result


def runtime_with_failure_diagnostics(cli, home, run, foreground_launch=None):
    try:
        return exercise_runtime(cli, home, run, foreground_launch=foreground_launch)
    except Exception:
        # Emit while the temporary home still exists, after lifecycle cleanup.
        # Collection itself must never replace the original nonzero failure.
        try:
            print(json.dumps(collect_runtime_failure(home), ensure_ascii=True), file=sys.stderr, flush=True)
        except Exception as error:
            print(json.dumps({'scope': 'synthetic_windows_runtime_fixture',
                              'diagnostic_error': type(error).__name__}), file=sys.stderr, flush=True)
        raise


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


def exercise_runtime(cli, home, run, foreground_launch=None):
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
    foreground = None
    foreground_owned = False
    mode = 'detached'
    try:
        attempted_start = True  # Also clean up a partially successful launch.
        try:
            started = command('start')
        except PackagedCommandError as error:
            if foreground_launch is None or not verified_host_job_restriction(error):
                raise
            rejected = command('status')
            if rejected.get('state') != 'stopped' or rejected.get('running') is not False:
                raise ValueError('Rejected detached launch left an unexpected runtime') from error
            mode = 'supervised_foreground_in_restricted_ci_job'
            print('::warning::Detached launch rejected by CI host Job; testing production runtime run inside that Job. Explorer independent lifetime remains untested.')
            foreground = foreground_launch(cli)
            started = None
        if started is not None and started.get('running') is not True:
            raise ValueError('Supervisor start was not acknowledged')
        # Only detached mode proves survival of the launcher CLI's exit.
        deadline = time.monotonic()+25
        while True:
            if foreground is not None and foreground.poll() is not None:
                raise RuntimeError('Supervised foreground runtime exited before readiness')
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
        if foreground is not None:
            if first.get('identity', {}).get('pid') != foreground.pid:
                raise ValueError('Runtime identity does not belong to retained foreground process')
            foreground_owned = True
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
            may_stop = foreground is None or foreground_owned
            if foreground is not None and not may_stop:
                try:
                    may_stop = read_private_json(record_path).get('identity', {}).get('pid') == foreground.pid
                except (OSError, ValueError):
                    may_stop = False
            if may_stop:
                try:
                    stopped = command('stop')
                    if stopped.get('running') is not False or stopped.get('state') != 'stopped':
                        raise ValueError('Supervisor stop was not confirmed')
                    if command('status').get('state') != 'stopped' or record_path.exists():
                        raise ValueError('Supervisor state persisted after shutdown')
                except Exception:
                    cleanup_errors.append('runtime cleanup failed')
        if foreground is not None:
            # Stop via authenticated CLI first. On failed startup/shutdown, only
            # the exact Popen handle created by this fixture may be terminated.
            try:
                try:
                    exitcode = foreground.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    foreground.terminate()
                    exitcode = foreground.wait(timeout=5)
                    cleanup_errors.append('foreground required forced cleanup')
                if exitcode != 0:
                    cleanup_errors.append('foreground exited with failure')
            except Exception:
                cleanup_errors.append('foreground process cleanup failed')
        if cleanup_errors:
            raise RuntimeError('; '.join(cleanup_errors))
    if (home/'.config/xueqiu/ble-pairing.json').exists():
        raise ValueError('Runtime smoke unexpectedly created Bluetooth credentials')
    return {'state': 'verified', 'mode': mode,
            'detached_launch': 'verified_in_ci' if mode == 'detached' else 'rejected_by_ci_host_job',
            'explorer_independent_lifetime': 'not_tested',
            'supervisor': 'survived_launcher_exit' if mode == 'detached' else 'foreground_lifecycle_verified',
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
                raise PackagedCommandError(args, result)
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
        runtime = runtime_with_failure_diagnostics(installed['cli'], home, run,
            foreground_launch=lambda executable: launch_foreground_in_ci(executable, home, env))
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
