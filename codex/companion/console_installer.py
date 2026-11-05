"""Interactive, agent-free installation; fixed operations behind the console's auth.

No host trust edits, firmware writes, account tokens or arbitrary shell commands.
Unselected features leave existing configuration unchanged.
"""
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import threading
import time

from console_backend import SetupError, child_options, interpreter
from private_files import private_dir, private_file, private_write, read_text

ADAPTERS = ('codex', 'claude', 'cursor')
FEATURES = ('connection', 'autostart', 'speech')
STEPS = ('environment', 'codex', 'claude', 'cursor', 'autostart', 'connection', 'speech')


def specialize_hooks(root, python):
    path = Path(root) / 'hooks/hooks.json'
    data = json.loads(path.read_text())
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('command'), str) and value['command'].startswith('python3 '):
                value['command'] = shlex.quote(str(python)) + ' -X utf8 ' + value['command'][8:]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def command_for(name, explicit=''):
    if name not in ('codex', 'claude'):
        return ''
    candidates = []
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_absolute() or not p.is_file() or p.suffix.lower() not in ('', '.exe', '.cmd'):
            raise SetupError('host_path_invalid')
        return str(p.resolve())
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    if sys.platform == 'darwin':
        candidates += [Path('/Applications/Codex.app/Contents/Resources/codex')] if name == 'codex' else []
        candidates += [Path.home() / '.local/bin' / name, Path('/opt/homebrew/bin') / name, Path('/usr/local/bin') / name]
    elif os.name == 'nt':
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local')))
        if name == 'codex':
            candidates += list(base.glob('Programs/*Codex*/resources/codex.exe'))
            try:
                r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                    "Get-AppxPackage *Codex* | Select-Object -ExpandProperty InstallLocation"],
                    capture_output=True, timeout=12, **child_options())
                for line in r.stdout.decode('utf-8-sig', errors='replace').splitlines():
                    p = Path(line.strip())
                    if p.is_absolute():
                        candidates += [p / 'app/resources/codex.exe', p / 'resources/codex.exe']
            except (OSError, subprocess.TimeoutExpired):
                pass
        candidates += [Path.home() / '.local/bin' / (name + '.exe')]
    elif name == 'codex':
        candidates += [Path('/usr/lib/chatgpt/resources/codex')]
    return next((str(p) for p in candidates if p.is_file()), '')


def validate_choices(data):
    if set(data) != {'adapters', 'features', 'speech_model', 'commands'}:
        raise SetupError('invalid_request')
    adapters, features, commands = data['adapters'], data['features'], data['commands']
    if (not isinstance(adapters, list) or any(not isinstance(x, str) or x not in ADAPTERS for x in adapters)
            or len(set(adapters)) != len(adapters) or not isinstance(features, dict)
            or set(features) != set(FEATURES) or any(type(v) is not bool for v in features.values())
            or not isinstance(data['speech_model'], str) or data['speech_model'] not in ('tiny', 'base', 'small')
            or not isinstance(commands, dict) or set(commands) != {'codex', 'claude'}
            or any(not isinstance(v, str) or len(v) > 2048 or any(c in v for c in '\r\n\x00') for v in commands.values())):
        raise SetupError('invalid_request')
    if not adapters and not any(features.values()):
        raise SetupError('nothing_selected')
    if os.name == 'nt' and any(a in adapters for a in ('claude', 'cursor')):
        raise SetupError('adapter_platform_unsupported')
    return json.loads(json.dumps(data))


class Installer:
    def __init__(self, backend):
        self.backend = backend
        self.lock = threading.RLock()
        self.worker = None
        self.path = backend.state / 'console/installer.json'
        self.value = None
        try:
            value = json.loads(read_text(self.path))
            validate_choices(value['choices'])
            if value.get('schema') != 1 or set(value['steps']) != set(STEPS):
                raise ValueError('invalid state')
            for step in value['steps'].values():
                if step['state'] == 'running':
                    step.update(state='failed', code='interrupted')
            self.value = value
        except (OSError, ValueError, KeyError, TypeError, SetupError):
            pass

    def available(self):
        package = self.backend.root.parents[1]
        return {'codex': (package / 'codex/tools/install_plugin.py').is_file(),
                'claude': os.name != 'nt' and (package / 'claude/.claude-plugin/marketplace.json').is_file(),
                'cursor': os.name != 'nt' and (package / 'cursor/snowball-companion/.cursor-plugin/plugin.json').is_file()}

    def public(self):
        with self.lock:
            running = bool(self.worker and self.worker.is_alive())
            value = json.loads(json.dumps(self.value or {}))
        return dict(value, started=bool(value), running=running, available=self.available(),
                    complete=bool(value) and all(s['state'] in ('ready', 'skipped') for s in value['steps'].values()))

    def save(self):
        private_write(self.path, (json.dumps(self.value, ensure_ascii=False) + '\n').encode())

    def step(self, name, state, code):
        with self.lock:
            self.value['steps'][name] = {'state': state, 'code': code}
            self.save()

    def start(self, data):
        choices = validate_choices(data)
        with self.backend.lock, self.lock:
            if (self.worker and self.worker.is_alive() or self.backend.setup.public().get('local_running')
                    or self.backend.job and self.backend.job.get('state') == 'running'):
                raise SetupError('job_busy')
            available = self.available()
            if any(not available[a] for a in choices['adapters']):
                raise SetupError('adapter_not_bundled')
            self.value = {'schema': 1, 'choices': choices, 'steps': {
                name: {'state': 'pending' if name == 'environment' or name in choices['adapters']
                       or choices['features'].get(name) else 'skipped', 'code': 'pending'} for name in STEPS}}
            self.save()
            self.worker = threading.Thread(target=self.run, daemon=True)
            self.worker.start()
        return self.public()

    def run_command(self, arguments, *, timeout=180):
        folder = private_dir(self.backend.state / 'console')
        log_path = folder / 'installer.log'
        with log_path.open('ab') as log:
            private_file(log_path)
            result = subprocess.run(list(map(str, arguments)), stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, timeout=timeout, **child_options())
        if result.returncode:
            raise SetupError('host_install_failed')

    def install_adapter(self, name, choices):
        package = self.backend.root.parents[1]
        python = interpreter(self.backend.root)
        if name == 'codex':
            cli = command_for(name, choices['commands'][name])
            if not cli:
                raise SetupError('host_cli_missing')
            self.run_command([python, '-X', 'utf8', package / 'codex/tools/install_plugin.py',
                              '--codex', cli, '--market-root', package / 'codex'])
        elif name == 'claude':
            cli = command_for(name, choices['commands'][name])
            if not cli:
                raise SetupError('host_cli_missing')
            configured = package / 'configured-markets/claude'
            if not configured.exists():
                shutil.copytree(package / 'claude', configured)
                specialize_hooks(configured / 'plugins/snowball-companion', python)
            self.run_command([cli, 'plugin', 'marketplace', 'add', configured])
            self.run_command([cli, 'plugin', 'install', 'snowball-companion@snowball-local', '--scope', 'user'])
        elif name == 'cursor':
            target = Path.home() / '.cursor/plugins/local/snowball-companion'
            source = package / 'cursor/snowball-companion'
            if target.exists() or target.is_symlink():
                # Existing adapters may be in use or host a newer shared runtime.
                a = target / '.cursor-plugin/plugin.json'
                b = source / '.cursor-plugin/plugin.json'
                if not target.is_symlink() and a.is_file() and a.read_bytes() == b.read_bytes():
                    return
                raise SetupError('existing_adapter_preserved')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target)
            specialize_hooks(target, python)

    def run(self):
        choices = self.value['choices']
        for name in STEPS:
            if self.value['steps'][name]['state'] != 'pending':
                continue
            if name not in ('environment',) and self.value['steps']['environment']['state'] != 'ready':
                self.step(name, 'failed', 'dependencies_missing')
                continue
            self.step(name, 'running', 'checking')
            try:
                if name in ADAPTERS:
                    self.install_adapter(name, choices)
                else:
                    kwargs = {'speech_model': choices['speech_model']} if name == 'speech' else {}
                    self.backend.configure_feature(name, lambda code: self.step(name, 'running', code), **kwargs)
                self.step(name, 'ready', 'host_review_required' if name in ADAPTERS else 'connection_started' if name == 'connection' else 'ready')
            except SetupError as error:
                self.step(name, 'failed', str(error))
            except subprocess.TimeoutExpired:
                self.step(name, 'failed', 'operation_timeout')
            except Exception:
                self.step(name, 'failed', 'operation_failed')
