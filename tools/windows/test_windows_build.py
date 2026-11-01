"""Platform-independent payload/spec checks; native EXE smoke runs in Windows CI."""
import ast
import base64
import hashlib
import io
import json
import importlib.util
import sys
import os
from pathlib import Path
import re
import struct
import tempfile
import unittest
import zipfile
from types import SimpleNamespace
from unittest.mock import patch
import stage_payload as stage
from smoke_distribution import pe_subsystem, run_distribution, exercise_runtime, collect_runtime_failure, runtime_with_failure_diagnostics, PackagedCommandError, verified_host_job_restriction, launch_foreground_in_ci, collect_task_failure, emit_task_failure
import distribution_manifest as native

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT/'codex/companion'))


class WindowsBuildTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root/'payload'
        self.files = {name: b'# reviewed fixture\n' for name in stage.REQUIRED}
        self.files.update({'README.md': b'fixture', 'BUNDLE.json': b'{"platform":"codex"}'})

    def build(self):
        with patch.object(stage, 'bundle_inputs', return_value=self.files) as inputs:
            listing = stage.stage(self.root, self.output)
            inputs.assert_called_once_with(self.root, 'codex')
        return listing

    def test_payload_uses_only_reviewed_bundle_inputs(self):
        (self.root/'credentials.json').write_text('fixture-never-copied')
        (self.root/'firmware.bin').write_bytes(b'fixture-never-copied')
        listing = self.build()
        self.assertEqual(set(listing), set(self.files))
        self.assertEqual(stage.verify(self.output), listing)
        self.assertFalse((self.output/'credentials.json').exists())
        self.assertFalse((self.output/'firmware.bin').exists())
        for name, data in self.files.items():
            self.assertEqual(listing[name], {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})

    def test_missing_required_runtime_refuses_before_writes(self):
        del self.files['codex/companion/platform_support.py']
        with self.assertRaises(ValueError): self.build()
        self.assertFalse(self.output.exists())

    def test_existing_destination_is_never_merged(self):
        self.output.mkdir(); (self.output/'old').write_text('keep')
        with self.assertRaises(FileExistsError): self.build()
        self.assertEqual(list(self.output.iterdir()), [self.output/'old'])

    def test_private_path_is_rejected_even_if_upstream_regresses(self):
        for name in ('codex/credentials.json', 'codex/firmware/image.bin', '../escape.py'):
            with self.subTest(name=name):
                self.files[name] = b'fixture'
                with self.assertRaises(ValueError): self.build()
                self.assertFalse(self.output.exists())
                del self.files[name]

    def test_verify_rejects_tampering_and_extra_files(self):
        self.build()
        path = self.output/'README.md'
        path.write_text('changed')
        with self.assertRaises(ValueError): stage.verify(self.output)
        path.write_bytes(self.files['README.md'])
        extra = self.output/'unlisted.py'; extra.write_text('fixture')
        with self.assertRaises(ValueError): stage.verify(self.output)
        extra.unlink(); path.unlink()
        with self.assertRaises(ValueError): stage.verify(self.output)

    def test_spec_has_one_runtime_and_distinct_console_modes(self):
        tree = ast.parse((HERE/'Snowball.spec').read_text())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
        for name in ('Analysis', 'PYZ', 'COLLECT'):
            self.assertEqual(sum(n.func.id == name for n in calls), 1)
        executables = [n for n in calls if n.func.id == 'EXE']
        self.assertEqual(len(executables), 2)
        modes = {next(k.value.value for k in n.keywords if k.arg == 'name'):
                 next(k.value.value for k in n.keywords if k.arg == 'console') for n in executables}
        self.assertEqual(modes, {'Snowball': False, 'SnowballCLI': True})
        source = (HERE/'Snowball.spec').read_text()
        self.assertIn("collect_submodules('winrt')", source)
        self.assertIn("collect_all('cryptography')", source)
        self.assertIn("'tkinter'", source)
        self.assertIn("'serial.tools.list_ports_windows'", source)

    def test_all_dependencies_are_exactly_pinned(self):
        lines = [line for line in (HERE/'requirements-build.txt').read_text().splitlines()
                 if line and not line.startswith('#')]
        self.assertTrue(all(re.fullmatch(r'[A-Za-z0-9_.-]+==[0-9][A-Za-z0-9.!+-]*', line) for line in lines))
        self.assertEqual(len(lines), len({line.split('==')[0] for line in lines}))
        self.assertTrue(all(line.endswith('==3.2.1') for line in lines if line.startswith('winrt-')))

    def test_explicit_ci_suite_does_not_pick_up_linux_only_tests(self):
        tests = json.loads((HERE/'ci_tests.json').read_text())
        self.assertEqual(len(tests), len(set(tests)))
        self.assertIn('codex/tools/test_platform_support.py', tests)
        self.assertIn('codex/companion/test_windows_runtime.py', tests)
        self.assertIn('codex/companion/test_windows_app.py', tests)
        self.assertIn('codex/companion/test_pairing_store.py', tests)
        self.assertTrue(all('*' not in name and name.endswith('.py') for name in tests))
        self.assertNotIn('codex/companion/test_reconnect.py', tests)
        script = (HERE/'ci.ps1').read_text()
        self.assertIn('WindowsBuiltInRole]::Administrator', script)
        self.assertIn('ACL checks are not bypassed', script)

    def test_native_manifest_matches_installer_and_rejects_extra_files(self):
        self.output.mkdir()
        (self.output/'Snowball.exe').write_bytes(b'GUI fixture')
        (self.output/'SnowballCLI.exe').write_bytes(b'CLI fixture')
        internal = self.output/'_internal'; internal.mkdir()
        (internal/'base_library.zip').write_bytes(b'Runtime fixture')
        value = native.generate(self.output)
        self.assertEqual(value['schema'], 1)
        self.assertEqual(value['product'], 'SnowballCompanion')
        self.assertEqual(value['version'], '0.6.0')
        self.assertNotIn(native.MANIFEST, value['files'])
        self.assertEqual(native.verify(self.output), value)
        # Validate the exact contract against the installer's production code.
        spec = importlib.util.spec_from_file_location('windows_install_manifest_test', ROOT/'codex/companion/windows_install.py')
        module = importlib.util.module_from_spec(spec)
        with patch.object(sys, 'path', [str(ROOT/'codex/companion'), *sys.path]):
            spec.loader.exec_module(module)
        self.assertEqual(module.validate_bundle(self.output), value)
        archive = self.root/'candidate.zip'
        native.archive(self.output, archive)
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(set(zipped.namelist()), {'Snowball/'+name for name in value['files']} | {'Snowball/'+native.MANIFEST})
        with self.assertRaises(ValueError): native.archive(self.output, archive)
        extra = internal/'unexpected.txt'; extra.write_text('fixture')
        with self.assertRaises(ValueError): native.verify(self.output)
        with self.assertRaises(ValueError): module.validate_bundle(self.output)
        extra.unlink()
        (self.output/'SnowballCLI.exe').write_bytes(b'changed')
        with self.assertRaises(ValueError): native.verify(self.output)
        with self.assertRaises(FileExistsError): native.generate(self.output)

    def test_native_manifest_refuses_credentials_and_missing_executable(self):
        self.output.mkdir()
        (self.output/'Snowball.exe').write_bytes(b'GUI fixture')
        with self.assertRaises(ValueError): native.generate(self.output)
        (self.output/'SnowballCLI.exe').write_bytes(b'CLI fixture')
        (self.output/'hub-token').write_bytes(b'fixture')
        with self.assertRaises(ValueError): native.generate(self.output)
        self.assertFalse((self.output/native.MANIFEST).exists())

    def test_smoke_installs_only_into_isolated_unicode_appdata(self):
        distribution = self.root/'distribution'; distribution.mkdir()
        (distribution/'Snowball.exe').write_bytes(b'GUI fixture')
        (distribution/'SnowballCLI.exe').write_bytes(b'CLI fixture')
        script = '# Synthetic launcher fixture\n'
        command = 'powershell.exe -NoProfile -NonInteractive -EncodedCommand ' + base64.b64encode(script.encode('utf-16-le')).decode()
        events = ('SessionStart', 'UserPromptSubmit', 'Stop')
        self.files['codex/plugins/xueqiu-status/scripts/windows_hook.ps1'] = script.encode()
        self.files['codex/plugins/xueqiu-status/hooks/hooks.json'] = json.dumps({
            'hooks': {event: [{'hooks': [{'commandWindows': command}]}] for event in events}}).encode()
        with patch.object(stage, 'bundle_inputs', return_value=self.files):
            stage.stage(self.root, distribution/'_internal/bundled')
        native.generate(distribution)
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv)
            if argv[1] == '--version': output = 'Snowball Companion 0.6.0\n'
            elif argv[1:3] == ['pet', 'list']: output = json.dumps({'characters': [{'id': 'snowball', 'name': '雪球'}]})
            elif argv[1:3] == ['doctor', '--json']: output = json.dumps({'checks': [{'code': 'dependencies', 'versions': {'bleak': '3.0.2', 'pyserial': '3.5'}}]})
            elif argv[1:3] == ['gui', '--smoke-test']: output = ''
            else:
                self.assertTrue(argv[1] == 'hook' or argv[1:3] == ['-NoProfile', '-NonInteractive'])
                env = kwargs['env']; payload = json.loads(kwargs['input'])
                self.assertIn('合成', payload['session_id'])
                identity = hashlib.sha256(payload['session_id'].encode()).hexdigest()[:32]
                record = Path(env['USERPROFILE'])/'.local/state/xueqiu/codex-adapter/hooks'/(identity+'.json')
                record.parent.mkdir(parents=True, exist_ok=True)
                seq = json.loads(record.read_text()).get('seq', 0) if record.exists() else 0
                record.write_text(json.dumps({'seq': seq+1}))
                if argv[1] == '-NoProfile':
                    self.assertNotIn('python', env['PATH'].lower())
                    self.assertTrue(Path(env['PLUGIN_ROOT']).is_relative_to(Path(env['USERPROFILE'])))
                output = '{}'
            return SimpleNamespace(returncode=0, stdout=output, stderr='')
        with patch('smoke_distribution.pe_subsystem', side_effect=lambda path: 3 if path.name=='SnowballCLI.exe' else 2), \
             patch('smoke_distribution.subprocess.run', side_effect=execute), \
             patch.dict(os.environ, {'SYSTEMROOT': str(self.root/'Windows')}), \
             patch('smoke_distribution.exercise_runtime', return_value={'state': 'fixture_verified'}) as lifecycle:
            result = run_distribution(distribution)
        self.assertEqual(result['hook_events'], 6)
        self.assertEqual(result['isolated_install'], 'ok')
        self.assertEqual(result['commandWindows'], 'ok')
        self.assertTrue(any(argv[1:3] == ['pet', 'list'] for argv in calls))
        self.assertTrue(any(argv[1:3] == ['doctor', '--json'] for argv in calls))
        self.assertEqual(sum(argv[1:3] == ['-NoProfile', '-NonInteractive'] for argv in calls), 3)
        lifecycle.assert_called_once()
        self.assertEqual(result['runtime_lifecycle']['state'], 'fixture_verified')

    def test_failure_diagnostics_tail_only_synthetic_logs(self):
        directory = self.root/'.local/state/xueqiu/windows-runtime'
        directory.mkdir(parents=True)
        (directory/'supervisor.log').write_bytes(b'prefix-not-in-tail'+b'x'*16384)
        (directory/'hub.log').write_bytes(b'synthetic Hub traceback')
        (directory/'bridge.log').write_bytes(b'synthetic bridge traceback')
        (directory/'runtime.json').write_text('DO_NOT_READ_CONTROL_RECORD')
        (directory/'hub-token').write_text('DO_NOT_READ_TOKEN')
        with patch('smoke_distribution.current_job_diagnostic', return_value={'in_job': True}):
            value = collect_runtime_failure(self.root)
        encoded = json.dumps(value)
        self.assertNotIn('DO_NOT_READ', encoded)
        self.assertNotIn('prefix-not-in-tail', encoded)
        self.assertEqual(value['current_process_job'], {'in_job': True})
        self.assertEqual(value['log_tails']['supervisor.log']['bytes_read'], 16384)
        self.assertEqual(value['log_tails']['supervisor.log']['tail'], 'x'*16384)
        self.assertEqual(set(value['log_tails']), {'supervisor.log', 'hub.log', 'bridge.log'})

    def test_diagnostic_collector_does_not_follow_log_hardlink(self):
        directory = self.root/'.local/state/xueqiu/windows-runtime'
        directory.mkdir(parents=True)
        record = directory/'runtime.json'; record.write_text('DO_NOT_READ_CONTROL_RECORD')
        os.link(record, directory/'supervisor.log')
        with patch('smoke_distribution.current_job_diagnostic', return_value={'in_job': False}):
            value = collect_runtime_failure(self.root)
        self.assertEqual(value['log_tails'], {})

    def test_runtime_failure_prints_diagnostic_but_stays_failure(self):
        error = RuntimeError('synthetic start failure')
        output = io.StringIO()
        with patch('smoke_distribution.exercise_runtime', side_effect=error), \
             patch('smoke_distribution.collect_runtime_failure', return_value={'fixture': 'trace'}), \
             patch('sys.stderr', output):
            with self.assertRaises(RuntimeError) as caught:
                runtime_with_failure_diagnostics('fixture-cli', self.root, None)
        self.assertIs(caught.exception, error)
        self.assertEqual(json.loads(output.getvalue()), {'fixture': 'trace'})

    def test_diagnostic_error_never_replaces_runtime_failure(self):
        error = RuntimeError('synthetic start failure')
        output = io.StringIO()
        with patch('smoke_distribution.exercise_runtime', side_effect=error), \
             patch('smoke_distribution.collect_runtime_failure', side_effect=PermissionError()), \
             patch('sys.stderr', output):
            with self.assertRaises(RuntimeError) as caught:
                runtime_with_failure_diagnostics('fixture-cli', self.root, None)
        self.assertIs(caught.exception, error)
        self.assertEqual(json.loads(output.getvalue())['diagnostic_error'], 'PermissionError')

    def test_task_diagnostic_queries_only_verified_current_user_name_and_keeps_bom(self):
        sid = 'S-1-5-21-synthetic'
        name = 'SnowballCompanion-'+hashlib.sha256(sid.encode()).hexdigest()[:16]
        api = SimpleNamespace(current=lambda: {'sid': sid}, task_tool=lambda: 'fixture-schtasks.exe', task_encoding=lambda: 'cp437')
        runtime = SimpleNamespace(_api=lambda: api, _task_name=lambda value: 'SnowballCompanion-'+hashlib.sha256(value.encode()).hexdigest()[:16])
        raw = '<?xml version="1.0"?><Task>合成 用户</Task>'.encode('utf-16')
        with patch.dict(sys.modules, {'windows_runtime': runtime}), \
             patch('smoke_distribution.subprocess.run', return_value=SimpleNamespace(returncode=0, stdout=raw)) as query:
            report = collect_task_failure(name, 'fixture-cli.exe')
        self.assertEqual(query.call_args.args[0], ['fixture-schtasks.exe', '/Query', '/TN', name, '/XML', '/HRESULT'])
        self.assertTrue(report['utf16_bom'])
        self.assertEqual(report['decoding'], 'utf-16')
        self.assertIn('合成 用户', report['xml_repr'])
        self.assertEqual(report['prefix_hex'], raw[:16].hex())
        with patch.dict(sys.modules, {'windows_runtime': runtime}), \
             patch('smoke_distribution.subprocess.run') as query:
            with self.assertRaisesRegex(ValueError, 'identity changed'):
                collect_task_failure('some-other-task', 'fixture-cli.exe')
        query.assert_not_called()

    def test_task_diagnostic_total_emission_is_at_most_16k(self):
        output = io.StringIO()
        with patch('smoke_distribution.collect_task_failure', return_value={
                'scope': 'synthetic_current_user_task', 'xml_repr': repr('合成\n'*16000)}), \
             patch('sys.stderr', output):
            emit_task_failure('fixture-task', 'fixture-cli')
        self.assertLessEqual(len(output.getvalue().encode()), 16384)
        self.assertTrue(json.loads(output.getvalue())['truncated'])

    def test_pe_architecture_and_gui_console_subsystem(self):
        data = bytearray(256); data[:2] = b'MZ'
        struct.pack_into('<I', data, 0x3c, 0x80)
        data[0x80:0x84] = b'PE\0\0'
        struct.pack_into('<H', data, 0x84, 0x8664)
        struct.pack_into('<H', data, 0x98, 0x20b)
        for subsystem in (2, 3):
            struct.pack_into('<H', data, 0x98+68, subsystem)
            path = self.root/'fixture.exe'; path.write_bytes(data)
            self.assertEqual(pe_subsystem(path), subsystem)
        struct.pack_into('<H', data, 0x84, 0x14c); path.write_bytes(data)
        with self.assertRaises(ValueError): pe_subsystem(path)


class RuntimeSmokeTests(unittest.TestCase):
    """Only mocks here: the installed CLI/HTTP branches run for real in CI."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.calls = []
        self.running = self.task = False
        self.identity = {'pid': 42, 'start': '100'}
        self.fail_install = self.fail_remove = self.fail_start = self.duplicate_start = False
        self.fail_stop = False
        self.job_report = None
        self.start_count = 0
        self.patches = [
            patch('smoke_distribution.runtime_session_id', return_value=1),
            patch('smoke_distribution.time.sleep'),
            patch('smoke_distribution.read_private_json', side_effect=self.read),
            patch('smoke_distribution.reject_unsigned_control'),
            patch('smoke_distribution.verify_hub_health'),
            patch('smoke_distribution.emit_task_failure'),
        ]
        self.mocks = [p.start() for p in self.patches]
        for p in self.patches: self.addCleanup(p.stop)

    def read(self, path):
        if path.name == 'runtime.json':
            return {'identity': dict(self.identity), 'port': 4199, 'install': 'fixture-install'}
        return {'state': 'error', 'error': 'missing_credentials'}

    def snapshot(self):
        return {'state': 'running' if self.running else 'stopped', 'running': self.running,
                'children': {'hub': {'pid': 43, 'healthy': True}} if self.running else {}}

    def execute(self, cli, *args):
        self.assertEqual(cli, 'fixture-cli.exe'); self.assertEqual(args[0], 'runtime')
        op = args[1]; self.calls.append(op)
        if op == 'status': value = self.snapshot()
        elif op == 'start':
            self.start_count += 1
            if self.job_report is not None and self.start_count == 1:
                raise PackagedCommandError(('runtime', 'start'), SimpleNamespace(returncode=2, stderr=json.dumps(self.job_report)))
            self.running = True
            if self.fail_start: raise RuntimeError('synthetic partial start failure')
            if self.duplicate_start and self.start_count > 1: self.identity['pid'] = 99
            value = self.snapshot()
        elif op == 'stop':
            if self.fail_stop: raise RuntimeError('synthetic stop failure')
            self.running = False; value = self.snapshot()
        elif op == 'autostart-status':
            value = {'state': 'owned' if self.task else 'absent', 'owned': self.task, 'task': 'fixture-task'}
        elif op == 'install-autostart':
            self.task = True
            if self.fail_install: raise RuntimeError('synthetic partial task registration failure')
            value = {'installed': True, 'interactive': True, 'elevated': False, 'task': 'fixture-task'}
        elif op == 'remove-autostart':
            if self.fail_remove: raise RuntimeError('synthetic removal failure')
            self.task = False; value = {'removed': True}
        else: self.fail('Unexpected runtime action '+op)
        return SimpleNamespace(stdout=json.dumps(value))

    def invoke(self): return exercise_runtime('fixture-cli.exe', self.home, self.execute)

    def test_real_branch_contract_checks_hmac_health_and_cleanup(self):
        value = self.invoke()
        self.assertEqual(value['state'], 'verified')
        self.assertEqual(value['mode'], 'detached')
        self.assertEqual(value['supervisor'], 'survived_launcher_exit')
        self.assertEqual(value['bridge'], 'missing_credentials')
        self.assertEqual(self.calls.count('start'), 2)
        self.assertEqual(self.calls[-4:], ['remove-autostart', 'autostart-status', 'stop', 'status'])
        self.assertFalse(self.running); self.assertFalse(self.task)
        self.mocks[3].assert_called_once_with(4199)
        self.mocks[4].assert_called_once_with(self.home)

    def test_session_zero_reports_skip_without_starting_anything(self):
        self.mocks[0].return_value = 0
        value = self.invoke()
        self.assertEqual(value['state'], 'skipped')
        self.assertEqual(value['reason'], 'session_0_noninteractive')
        self.assertEqual(self.calls, [])

    def test_partial_task_registration_still_removes_owned_task_and_stops(self):
        self.fail_install = True
        with self.assertRaisesRegex(RuntimeError, 'partial task'): self.invoke()
        self.assertIn('remove-autostart', self.calls)
        self.assertIn('stop', self.calls)
        self.assertFalse(self.task); self.assertFalse(self.running)

    def test_original_task_registration_failure_survives_cleanup_failure(self):
        self.fail_install = self.fail_remove = True
        output = io.StringIO()
        with patch('sys.stderr', output):
            with self.assertRaisesRegex(RuntimeError, 'synthetic partial task registration failure') as failure:
                self.invoke()
        self.assertIn('autostart cleanup failed', output.getvalue())
        self.assertTrue(any('cleanup also failed' in note for note in failure.exception.__notes__))
        self.mocks[-1].assert_called_once_with('fixture-task', 'fixture-cli.exe')
        self.assertIn('stop', self.calls)
        self.assertFalse(self.running)

    def test_partial_start_still_attempts_stop_but_never_registers_task(self):
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'partial start'): self.invoke()
        self.assertIn('stop', self.calls); self.assertNotIn('install-autostart', self.calls)
        self.assertFalse(self.running)

    def test_duplicate_supervisor_is_a_failure_and_still_stops(self):
        self.duplicate_start = True
        with self.assertRaisesRegex(ValueError, 'second supervisor'): self.invoke()
        self.assertIn('stop', self.calls); self.assertNotIn('install-autostart', self.calls)
        self.assertFalse(self.running)

    def test_task_cleanup_error_does_not_prevent_runtime_cleanup(self):
        self.fail_remove = True
        with self.assertRaisesRegex(RuntimeError, 'autostart cleanup failed'): self.invoke()
        self.assertIn('stop', self.calls); self.assertFalse(self.running)

    @staticmethod
    def restricted_report():
        return {'ok': False, 'operation': 'start', 'code': 'host_job_restricted',
                'winerror': 5, 'in_job': True, 'breakaway_allowed': False, 'silent_breakaway': False}

    def foreground_process(self):
        test = self
        class Process:
            pid = 42
            terminated = False
            waited = []
            def poll(self): return None if test.running else 0
            def wait(self, timeout):
                self.waited.append(timeout)
                if test.running: raise __import__('subprocess').TimeoutExpired('fixture-runtime', timeout)
                return 0
            def terminate(self): self.terminated = True; test.running = False
        self.running = True
        return Process()

    def test_verified_restricted_job_runs_foreground_without_claiming_detached_lifetime(self):
        self.job_report = self.restricted_report()
        processes = []
        def launch(cli):
            self.assertEqual(cli, 'fixture-cli.exe')
            processes.append(self.foreground_process()); return processes[-1]
        with patch('smoke_distribution.current_job_diagnostic', return_value={'available': True, 'in_job': True}):
            value = exercise_runtime('fixture-cli.exe', self.home, self.execute, foreground_launch=launch)
        self.assertEqual(value['mode'], 'supervised_foreground_in_restricted_ci_job')
        self.assertEqual(value['detached_launch'], 'rejected_by_ci_host_job')
        self.assertEqual(value['supervisor'], 'foreground_lifecycle_verified')
        self.assertEqual(value['explorer_independent_lifetime'], 'not_tested')
        self.assertEqual(len(processes), 1); self.assertFalse(processes[0].terminated)
        self.assertTrue(processes[0].waited)
        self.assertFalse(self.running); self.assertFalse(self.task)
        self.assertEqual(self.calls.count('start'), 2)

    def test_restriction_requires_every_safe_field_and_independent_job_membership(self):
        valid = self.restricted_report()
        reports = [{k: v for k, v in valid.items() if k != field} for field in valid]
        reports += [{**valid, 'code': 'supervisor_spawn_failed'}, {**valid, 'winerror': 87},
                    {**valid, 'in_job': 1}, {**valid, 'breakaway_allowed': True}]
        with patch('smoke_distribution.current_job_diagnostic', return_value={'available': True, 'in_job': True}):
            for report in reports:
                error = PackagedCommandError(('runtime', 'start'), SimpleNamespace(returncode=2, stderr=json.dumps(report)))
                self.assertFalse(verified_host_job_restriction(error), report)
        error = PackagedCommandError(('runtime', 'start'), SimpleNamespace(returncode=2, stderr=json.dumps(valid)))
        for current in ({'available': False}, {'available': True, 'in_job': False}):
            with patch('smoke_distribution.current_job_diagnostic', return_value=current):
                self.assertFalse(verified_host_job_restriction(error))

    def test_unverified_spawn_failure_does_not_launch_foreground(self):
        self.job_report = {**self.restricted_report(), 'code': 'supervisor_spawn_failed'}
        with patch('smoke_distribution.current_job_diagnostic', return_value={'available': True, 'in_job': True}), \
             patch('smoke_distribution.launch_foreground_in_ci') as launch:
            with self.assertRaises(PackagedCommandError):
                exercise_runtime('fixture-cli.exe', self.home, self.execute, foreground_launch=launch)
        launch.assert_not_called()

    def test_foreground_task_failure_still_cleans_task_and_retained_process(self):
        self.job_report = self.restricted_report(); self.fail_install = True
        processes = []
        def launch(_): processes.append(self.foreground_process()); return processes[-1]
        with patch('smoke_distribution.current_job_diagnostic', return_value={'available': True, 'in_job': True}):
            with self.assertRaisesRegex(RuntimeError, 'partial task'):
                exercise_runtime('fixture-cli.exe', self.home, self.execute, foreground_launch=launch)
        self.assertFalse(self.task); self.assertFalse(self.running)
        self.assertTrue(processes[0].waited); self.assertFalse(processes[0].terminated)

    def test_foreground_failed_stop_terminates_only_retained_popen_and_still_fails(self):
        self.job_report = self.restricted_report(); self.fail_stop = True
        processes = []
        def launch(_): processes.append(self.foreground_process()); return processes[-1]
        with patch('smoke_distribution.current_job_diagnostic', return_value={'available': True, 'in_job': True}):
            with self.assertRaisesRegex(RuntimeError, 'runtime cleanup failed'):
                exercise_runtime('fixture-cli.exe', self.home, self.execute, foreground_launch=launch)
        self.assertEqual(len(processes), 1); self.assertTrue(processes[0].terminated)
        self.assertFalse(self.running); self.assertFalse(self.task)

    def test_foreground_popen_uses_production_run_and_no_breakaway_flags(self):
        with patch('smoke_distribution.subprocess.Popen', return_value='retained-process') as spawn:
            value = launch_foreground_in_ci('fixture-cli.exe', self.home, {'FIXTURE': 'yes'})
        self.assertEqual(value, 'retained-process')
        self.assertEqual(spawn.call_args.args[0], ['fixture-cli.exe', 'runtime', 'run'])
        self.assertEqual(spawn.call_args.kwargs['creationflags'], 0)
        self.assertEqual(spawn.call_args.kwargs['env'], {'FIXTURE': 'yes'})
        self.assertEqual(spawn.call_args.kwargs['cwd'], self.home)

    def test_preexisting_task_is_not_replaced_or_removed(self):
        self.task = True
        with self.assertRaisesRegex(ValueError, 'already has an autostart'): self.invoke()
        self.assertNotIn('start', self.calls); self.assertNotIn('remove-autostart', self.calls)
        self.mocks[-1].assert_not_called()
        self.assertTrue(self.task)


if __name__ == '__main__': unittest.main()
