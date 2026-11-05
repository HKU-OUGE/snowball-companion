"""Bounded local setup operations. No firmware writes or implicit re-pairing."""
import argparse
import hashlib
import http.client
import importlib.util
import ipaddress
import json
import os
import shutil
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading
import time
import uuid

from private_files import private_dir, private_file, private_write, read_text, safe_reader

ROOT = Path(__file__).resolve().parent
CONFIG = Path.home() / '.config/xueqiu'
STATE = Path.home() / '.local/state/xueqiu'


class SetupError(Exception):
    pass


def environment(root=ROOT):
    legacy = root.parent / '.venv'
    if (legacy / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).is_file():
        return legacy
    return Path.home() / '.local/share/xueqiu/console-venv'


def interpreter(root=ROOT):
    # Native installers carry an independently versioned portable interpreter.
    # Do not accidentally launch an older global console venv after an upgrade.
    bundled = Path(root).parents[1] / 'INSTALLER.json'
    if bundled.is_file():
        portable = bundled.parent / 'python' / ('python.exe' if os.name == 'nt' else 'bin/python3')
        if portable.is_file():
            return str(portable)
    candidate = environment(root) / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    return str(candidate if candidate.is_file() else Path(sys.executable))


def local_api(port, path, data=None, *, config=CONFIG, timeout=3):
    token = read_text(config / 'hub-token').strip()
    if not token or len(token) > 256:
        raise SetupError('local_identity_invalid')
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=timeout)
    try:
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode()
        connection.request('GET' if data is None else 'POST', path, body,
                           {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(32769)
        if len(raw) > 32768:
            raise SetupError('local_response_invalid')
        value = json.loads(raw)
        if response.status != 200:
            allowed = {'open_device_network_page', 'provision_busy', 'provision_schema', 'request_rejected'}
            raise SetupError(value.get('error') if value.get('error') in allowed else 'local_service_rejected')
        return value
    finally:
        connection.close()


def pairing(config=CONFIG):
    value = json.loads(read_text(config / 'ble-pairing.json'))
    if (value.get('v') != 1 or not isinstance(value.get('device'), str) or
            len(value['device']) > 64 or len(bytes.fromhex(value.get('key', ''))) != 32):
        raise SetupError('pairing_invalid')
    return value


def lan_addresses():
    values = set()
    try:
        values.update(row[4][0] for row in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    # UDP connect selects a local route; no application packet is transmitted.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(('192.0.2.1', 9))
            values.add(sock.getsockname()[0])
    except OSError:
        pass
    result = []
    for value in sorted(values):
        ip = ipaddress.ip_address(value)
        if ip.is_private and not (ip.is_loopback or ip.is_unspecified or ip.is_link_local):
            result.append(value)
    return result


def child_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}


def hub_health(config=CONFIG):
    try:
        value = local_api(4190, '/v1/health', config=config)
        return value.get('ok') is True and value.get('version') == 1 and value.get('hub_alive') is True
    except (OSError, ValueError, SetupError, http.client.HTTPException):
        return False


def network_status(config=CONFIG):
    try:
        value = local_api(4194, '/v1/status', config=config)
        return value if value.get('gateway') is True and value.get('device') == pairing(config)['device'] else None
    except (OSError, ValueError, SetupError, http.client.HTTPException):
        return None


class Backend:
    def __init__(self, root=ROOT, config=CONFIG, state=STATE):
        self.root, self.config, self.state = Path(root), Path(config), Path(state)
        self.lock = threading.RLock()
        self.job = None
        self.children = {}
        self.logs = {}
        self.enabled = False
        self.dependency_cache = (0, None)
        try:
            self.enabled = json.loads(read_text(self.state / 'console/services.json')).get('enabled') is True
        except (OSError, ValueError):
            pass
        from console_setup import SetupPlan
        self.setup = SetupPlan(self)
        from console_installer import Installer
        self.installer = Installer(self)

    def dependencies(self):
        at, cached = self.dependency_cache
        if cached is not None and time.monotonic() - at < 15:
            return cached
        try:
            code = "import importlib.util,json;print(json.dumps({n:importlib.util.find_spec(n) is not None for n in ('bleak','serial','cryptography')}))"
            result = subprocess.run([interpreter(self.root), '-X', 'utf8', '-c', code],
                                    capture_output=True, timeout=8, **child_options())
            value = json.loads(result.stdout) if result.returncode == 0 else {}
        except (OSError, ValueError, subprocess.TimeoutExpired):
            value = {}
        self.dependency_cache = (time.monotonic(), value)
        return value

    def snapshot(self):
        try:
            bound = pairing(self.config)['device']
        except (OSError, ValueError, SetupError):
            bound = ''
        network = network_status(self.config)
        safe_network = None
        if network:
            fields = ('state', 'mode', 'configured', 'sealed', 'window', 'active', 'ssid', 'ip', 'route')
            safe_network = {k: network.get(k) for k in ('ble', 'online', 'sync', 'provision', 'lan')}
            safe_network['network'] = {k: network.get('network', {}).get(k) for k in fields}
        with self.lock:
            job = dict(self.job) if self.job else None
        return {'platform': 'windows' if os.name == 'nt' else 'linux' if sys.platform == 'linux' else 'macos',
                'device': bound, 'paired': bool(bound), 'hub': hub_health(self.config),
                'dependencies': self.dependencies(), 'network': safe_network,
                'telemetry': self.telemetry(bound),
                'capabilities': self.capabilities(),
                'setup': self.setup.public(), 'pet_catalog': self.pet_catalog(),
                'installer': self.installer.public(),
                'speech': self.speech_status(),
                'addresses': lan_addresses(), 'job': job,
                'autostart_supported': os.name == 'nt' or sys.platform in ('linux', 'darwin'),
                'gateway_configured': (self.config / 'network/gateway.json').is_file()}

    def pet_catalog(self):
        try:
            path = self.root.parent / 'plugins/xueqiu-status/assets/pet/catalog.json'
            rows = json.loads(path.read_text(encoding='utf-8'))['characters']
            return [{'id': r['id'], 'name': r['name']} for r in rows if r['status'] == 'ready']
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def speech_status(self):
        from speech_profiles import PROFILES, profile_for
        value = {'ready': False, 'profile': None, 'download_mb': None, 'reload_supported': False}
        try:
            config = json.loads(read_text(self.config / 'speech.json'))
            profile = profile_for(config)
            value.update(profile=profile, download_mb=PROFILES.get(profile, {}).get('download_mb'))
            health = local_api(4191, '/v1/health', config=self.config, timeout=.6)
            value.update(ready=health.get('ready') is True, reload_supported=health.get('reload_supported') is True)
            if health.get('profile') in PROFILES:
                value['profile'] = health['profile']
        except (OSError, ValueError, TypeError, SetupError, http.client.HTTPException):
            pass
        return value

    def configure_feature(self, feature, progress, *, speech_model=None):
        if feature == 'environment':
            if not all(self.dependencies().get(k) for k in ('bleak', 'serial', 'cryptography')):
                self.execute('prepare', {}, progress=progress)
            if not all(self.dependencies().get(k) for k in ('bleak', 'serial', 'cryptography')):
                raise SetupError('dependencies_missing')
        elif feature == 'autostart':
            from console_login import enable
            enable(self.root)
        elif feature == 'connection':
            if not (self.config / 'ble-pairing.json').exists():
                ports = self.ports()
                if len(ports) != 1:
                    raise SetupError('usb_port_missing' if not ports else 'usb_port_ambiguous')
                progress('usb_binding')
                self.execute('bind', {'port': ports[0]['port']})
            if not (hub_health(self.config) and self.telemetry()['connected']):
                self.execute('services', {})
        elif feature == 'speech':
            model = speech_model or (self.setup.value or {}).get('speech_model', 'auto')
            status = self.speech_status()
            if status['ready'] and (model == 'auto' or status['profile'] == model):
                return
            log_path = private_dir(self.state / 'console') / 'speech-setup.log'
            if log_path.exists() and log_path.stat().st_size > 2_000_000:
                os.replace(log_path, log_path.with_suffix('.previous.log'))
            upgrade_required = False
            with log_path.open('ab') as log:
                private_file(log_path)
                process = subprocess.Popen([interpreter(self.root), '-X', 'utf8', str(self.root / 'setup-speech.py'), '--model', model],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **child_options())
                for raw in process.stdout:
                    log.write(raw)
                    if b'speech_worker_upgrade_required' in raw:
                        upgrade_required = True
                    if raw.startswith(b'XQSETUP '):
                        try:
                            stage = json.loads(raw[8:])['stage']
                            from resource_fetch import STAGES
                            if stage in {'speech_dependencies', 'speech_model', 'speech_starting', 'speech_sd_imported', *STAGES}:
                                progress(stage)
                        except (ValueError, KeyError, TypeError):
                            pass
                if process.wait():
                    raise SetupError('speech_worker_upgrade_required' if upgrade_required else 'speech_failed')
            until = time.monotonic() + 90
            while time.monotonic() < until:
                current = self.speech_status()
                if current['ready'] and (model == 'auto' or current['profile'] == model):
                    return
                time.sleep(.5)
            raise SetupError('speech_not_ready')
        else:
            raise SetupError('invalid_request')

    def capabilities(self):
        """Summarize the running Hub, not the console process's own app channel.

        Never return session IDs, titles, question contents or host endpoints.
        A stale snapshot cannot make an unavailable answer channel look ready.
        """
        empty = {'fresh': False, 'can_create': False, 'can_instruct': False,
                 'pending': 0, 'answerable': 0}
        try:
            with safe_reader(self.state / 'hub/snapshot.json') as stream:
                raw = stream.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                return empty
            value = json.loads(raw)
            age = time.time() - value.get('generated_at', 0)
            if value.get('hub_alive') is not True or not 0 <= age < 20:
                return empty
            rows = value.get('sessions', [])
            if not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows):
                return empty
            questions = [q for row in rows for q in row.get('questions', [])
                         if isinstance(q, dict) and q.get('status') != 'ignored']
            return {'fresh': True, 'can_create': value.get('can_create') is True,
                    'can_instruct': any(r.get('instruction_available') is True for r in rows),
                    'pending': len(questions),
                    'answerable': sum(q.get('answerable') is True for q in questions)}
        except (OSError, ValueError, TypeError, AttributeError):
            return empty

    def telemetry(self, device=None):
        try:
            device = device or pairing(self.config)['device']
            value = json.loads(read_text(self.state / 'device/latest.json'))
            age = time.time() - value.get('received_at', 0)
            online = (value.get('device') == device and value.get('source') == 'device' and
                      value.get('connected') is True and 0 <= age < 20)
            return {'connected': online, 'transport': value.get('transport') if online else None,
                    'status_current': online and value.get('status_current') is True}
        except (OSError, ValueError, TypeError, SetupError):
            return {'connected': False, 'transport': None, 'status_current': False}

    def ports(self):
        code = ("import json;from serial.tools import list_ports;"
                "print(json.dumps([{'port':p.device,'name':p.description} "
                "for p in list_ports.comports() if p.vid==0x303a]))")
        try:
            result = subprocess.run([interpreter(self.root), '-X', 'utf8', '-c', code],
                                    capture_output=True, timeout=10, **child_options())
            ports = json.loads(result.stdout) if result.returncode == 0 else []
            return ports[:16]
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []

    def launch(self, name, arguments=()):
        process = self.children.get(name)
        if process is not None and process.poll() is None:
            return
        folder = private_dir(self.state / 'console')
        if name in self.logs:
            self.logs[name].close()
        log = folder / (name + '.log')
        if log.exists() and log.stat().st_size > 2_000_000:
            os.replace(log, log.with_suffix('.previous.log'))
        self.logs[name] = log.open('ab', buffering=0)
        private_file(log)
        self.children[name] = subprocess.Popen(
            [interpreter(self.root), '-X', 'utf8', str(self.root / (name + '.py')), *map(str, arguments)],
            cwd=self.root, stdout=self.logs[name], stderr=subprocess.STDOUT, **child_options())

    def enable_services(self):
        if not all(self.dependencies().values()) or len(self.dependencies()) != 3:
            if hub_health(self.config) and (network_status(self.config) or self.telemetry()['connected']):
                return  # Existing healthy services need no second Python environment.
            raise SetupError('dependencies_missing')
        if not self.enabled:
            private_write(self.state / 'console/services.json', b'{"enabled":true}\n')
        self.enabled = True
        if os.name == 'nt':
            from windows_supervisor import running
            if not running():
                self.launch('windows_supervisor')
        else:
            if not hub_health(self.config):
                self.launch('status_hub')
            try:
                pairing(self.config)
            except (OSError, ValueError, SetupError):
                return
            active = False
            if sys.platform == 'linux':
                try:
                    active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', 'snowball-bridge.service'],
                                            capture_output=True, timeout=3).returncode == 0
                except (OSError, subprocess.TimeoutExpired):
                    pass
            remote = network_status(self.config)
            telemetry = self.telemetry()
            if not active and not (remote and remote.get('ble')) and not (telemetry['connected'] and telemetry['transport'] == 'ble'):
                self.launch('bridge', ('watch', '--source', self.state / 'hub/snapshot.json'))

    def maintenance(self):
        # Called only after an explicit Start services action, never by opening a page.
        if self.setup.public().get('local_running') or self.installer.public()['running']:
            return
        if self.enabled:
            self.enable_services()
        if (self.config / 'network/gateway.json').is_file() and hub_health(self.config) and not network_status(self.config):
            if self.enabled:
                self.launch('network_gateway')

    def await_service(self, network=False):
        end = time.monotonic() + 15
        while time.monotonic() < end:
            if network_status(self.config) if network else hub_health(self.config):
                return
            time.sleep(.25)
        raise SetupError('local_service_unavailable')

    def execute(self, action, data, progress=None):
        if action == 'prepare':
            target = environment(self.root)
            if not (target / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')).is_file():
                private_dir(target.parent)
                subprocess.run([sys.executable, '-m', 'venv', str(target)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True, timeout=120,
                               **child_options())
            from offline_resources import pip_args
            offline=pip_args(interpreter(self.root),'connection',[],self.root/'requirements.txt',progress=progress)
            uv=shutil.which('uv')
            if offline:
                has_pip=subprocess.run([interpreter(self.root),'-c','import pip'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**child_options()).returncode==0
                if not has_pip:subprocess.run([interpreter(self.root),'-m','ensurepip','--upgrade'],check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**child_options())
                install=offline
            elif uv:
                install=[uv,'pip','install','--python',interpreter(self.root),'-r',str(self.root/'requirements.txt')]
            else:
                has_pip=subprocess.run([interpreter(self.root),'-c','import pip'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**child_options()).returncode==0
                if not has_pip:subprocess.run([interpreter(self.root),'-m','ensurepip','--upgrade'],check=True,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**child_options())
                install=[interpreter(self.root),'-m','pip','install','--disable-pip-version-check','-r',str(self.root/'requirements.txt')]
            subprocess.run(install, check=True, timeout=600,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **child_options())
            self.dependency_cache = (0, None)
            return 'prepared'
        if action == 'bind':
            if (self.config / 'ble-pairing.json').exists():
                pairing(self.config)
                return 'pairing_reused'
            port = data.get('port')
            if not isinstance(port, str) or port not in [p['port'] for p in self.ports()]:
                raise SetupError('usb_port_missing')
            if sys.platform == 'linux' and Path(port).exists() and not os.access(port, os.R_OK | os.W_OK):
                raise SetupError('usb_permission_required')
            result = subprocess.run([interpreter(self.root), '-X', 'utf8', str(self.root / 'bridge.py'),
                                     '--config', str(self.config / 'ble-pairing.json'), 'pair-usb', '--port', port],
                                    capture_output=True, timeout=25, **child_options())
            if result.returncode:
                # Only a firmware rejection may ask for physical confirmation.
                # Never copy arbitrary serial output (which may contain keys).
                allowed = {'usb_pairing_confirmation_required', 'usb_device_not_ready',
                           'usb_no_response', 'usb_binding_rejected'}
                for line in (result.stderr or b'').decode('utf-8', errors='replace').splitlines():
                    if line.startswith('XQUSB_ERROR '):
                        try:
                            code = json.loads(line[len('XQUSB_ERROR '):]).get('code')
                        except (ValueError, AttributeError):
                            continue
                        if isinstance(code, str) and code in allowed:
                            raise SetupError(code)
                raise SetupError('usb_binding_failed')
            pairing(self.config)
            self.enable_services()
            return 'bound'
        if action == 'services':
            self.enable_services()
            self.await_service()
            return 'services_started'
        if action == 'network':
            if not all(self.dependencies().values()) or len(self.dependencies()) != 3:
                raise SetupError('dependencies_missing')
            bind = data.get('bind')
            if bind not in lan_addresses():
                raise SetupError('lan_address_missing')
            pairing(self.config)
            self.enable_services()
            self.await_service()
            # Cryptography runs in the prepared runtime even if the console started with system Python.
            result = subprocess.run([interpreter(self.root), '-X', 'utf8', str(self.root / 'console_backend.py'),
                                     'init-network', '--bind', bind], capture_output=True, timeout=20,
                                    **child_options())
            if result.returncode:
                raise SetupError('gateway_identity_conflict')
            self.launch('network_gateway') if not network_status(self.config) else None
            self.await_service(network=True)
            return 'network_started'
        if action == 'autostart':
            from console_login import enable
            enable(self.root)
            return 'autostart_enabled'
        if action == 'firewall':
            if os.name != 'nt':
                raise SetupError('autostart_windows_only')
            # The visible button authorizes only this helper. Windows still asks
            # the user for UAC approval; the console never stores an admin password.
            result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                     str(self.root / 'windows-lan-access.ps1'), '-Python', interpreter(self.root)],
                                    capture_output=True, timeout=120, **child_options())
            if result.returncode:
                raise SetupError('firewall_not_enabled')
            return 'firewall_enabled'
        if action == 'usb_access':
            # polkit owns the password dialog. The webpage never sees a password.
            # Only grant this user access to one currently enumerated Espressif tty.
            if sys.platform != 'linux':
                raise SetupError('authorization_unavailable')
            ports = self.ports()
            if len(ports) != 1:
                raise SetupError('usb_port_missing' if not ports else 'usb_port_ambiguous')
            port = Path(ports[0]['port']).resolve()
            if port.parent != Path('/dev') or not port.name.startswith(('ttyACM', 'ttyUSB')) or not stat.S_ISCHR(port.stat().st_mode):
                raise SetupError('usb_port_missing')
            pkexec, acl = Path('/usr/bin/pkexec'), Path('/usr/bin/setfacl')
            if not pkexec.is_file() or not acl.is_file():
                raise SetupError('authorization_unavailable')
            result = subprocess.run([str(pkexec), str(acl), '-m', f'u:{os.getuid()}:rw', str(port)],
                                    capture_output=True, timeout=120)
            if result.returncode or not os.access(port, os.R_OK | os.W_OK):
                raise SetupError('authorization_cancelled')
            return 'usb_access_granted'
        raise SetupError('unknown_action')

    def submit(self, action, data):
        if action not in {'prepare', 'bind', 'services', 'network', 'autostart', 'firewall', 'usb_access'}:
            raise SetupError('unknown_action')
        with self.lock:
            if self.installer.public()['running']:
                raise SetupError('job_busy')
            if self.job and self.job['state'] == 'running':
                raise SetupError('job_busy')
            plan = self.setup.public()
            if any(plan.get('steps', {}).get(k, {}).get('state') == 'running' for k in ('environment', 'autostart', 'connection')):
                raise SetupError('job_busy')
            self.job = {'id': uuid.uuid4().hex, 'action': action, 'state': 'running', 'result': None}
        def work():
            try:
                result = self.execute(action, data)
                state = 'done'
            except SetupError as error:
                result, state = str(error), 'failed'
            except Exception:
                result, state = 'operation_failed', 'failed'
            with self.lock:
                self.job.update(result=result, state=state)
        threading.Thread(target=work, daemon=True).start()
        return {'queued': True}

    def provision(self, data):
        if set(data) != {'ssid', 'password', 'mode', 'remote'}:
            raise SetupError('provision_schema')
        if (not isinstance(data['ssid'], str) or not 1 <= len(data['ssid'].encode()) <= 32 or
                not isinstance(data['password'], str) or not 8 <= len(data['password'].encode()) <= 63 or
                type(data['mode']) is not int or data['mode'] not in (1, 2) or
                not isinstance(data['remote'], str) or len(data['remote']) > 191):
            raise SetupError('provision_schema')
        if data['remote']:
            from network_security import endpoint
            try:
                endpoint(data['remote'])
            except ValueError:
                raise SetupError('remote_endpoint_invalid') from None
        return local_api(4194, '/v1/provision', data, config=self.config)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['init-network'])
    parser.add_argument('--bind', required=True)
    args = parser.parse_args()
    from network_security import create_gateway
    create_gateway(CONFIG / 'network', args.bind, pairing()['device'])
    print('Gateway identity ready.')
