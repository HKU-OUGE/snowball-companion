"""Persistent, explicit first-use configuration. Browser stays the single progress UI."""
import json
import re
import secrets
import threading
import time
import uuid

from console_backend import SetupError
from private_files import private_write, safe_reader
from resource_fetch import STAGES as RESOURCE_STAGES

STEPS = ('environment', 'autostart', 'connection', 'speech', 'pet')
STATES = {'pending', 'running', 'ready', 'failed', 'needs_user', 'skipped', 'unsupported'}
CODES = {'pending', 'checking', 'ready', 'interrupted', 'operation_failed',
         'usb_binding', 'usb_port_missing', 'usb_port_ambiguous', 'usb_binding_failed', 'dependencies_missing', 'autostart_failed', 'autostart_conflict', 'autostart_unsupported',
         'local_service_unavailable', 'speech_dependencies', 'speech_model', 'speech_starting',
         'speech_sd_imported', 'speech_failed', 'speech_not_ready', 'speech_worker_upgrade_required', 'agent_required', 'pet_working', 'pet_verified',
         'pet_auth_required', 'pet_failed', 'pet_unsupported', 'user_skipped'}
CODES |= RESOURCE_STAGES


class SetupPlan:
    def __init__(self, backend):
        self.backend = backend
        self.path = backend.state / 'console/setup-plan.json'
        self.lock = threading.RLock()
        self.worker = None
        self.value = None
        try:
            with safe_reader(self.path) as stream:
                raw = stream.read(32769)
            value = json.loads(raw) if len(raw) <= 32768 else None
            if (not isinstance(value, dict) or value.get('schema') != 1 or
                not re.fullmatch('[a-f0-9]{32}', value.get('id', '')) or
                not isinstance(value.get('grant'), str) or not 32 <= len(value['grant']) <= 128 or
                value.get('adapter') not in ('codex', 'claude', 'cursor', 'device') or
                value.get('speech_model', 'auto') not in ('auto', 'tiny', 'base', 'small') or
                set(value.get('steps', {})) != set(STEPS) or
                any(s.get('state') not in STATES or s.get('code') not in CODES for s in value['steps'].values())):
                raise ValueError('invalid plan')
            # A restart is never proof that a previous install/remote mutation succeeded.
            value['agent_at'] = 0
            for name, step in value['steps'].items():
                if step['state'] == 'running':
                    step.update(state='failed' if name != 'pet' else 'needs_user', code='interrupted')
            self.value = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save(self):
        private_write(self.path, (json.dumps(self.value, ensure_ascii=False) + '\n').encode())

    def public(self):
        with self.lock:
            if self.value is None:
                return {'started': False, 'steps': {}}
            v = self.value
            steps = json.loads(json.dumps(v['steps']))
            live = 0 <= time.time() - v.get('agent_at', 0) < 90
            if not live and steps['pet']['state'] in ('pending', 'running'):
                steps['pet'].update(state='needs_user', code='agent_required')
            return {'started': True, 'id': v['id'], 'adapter': v['adapter'],
                    'character': v.get('character', 'auto'), 'steps': steps,
                    'speech_model': v.get('speech_model', 'auto'),
                    'agent_online': live, 'local_running': bool(self.worker and self.worker.is_alive()),
                    'complete': all(s['state'] in ('ready', 'skipped', 'unsupported') for s in steps.values())}

    def set_step(self, name, state, code):
        assert name in STEPS and state in STATES and code in CODES
        with self.lock:
            self.value['steps'][name] = {'state': state, 'code': code, 'at': time.time()}
            self.save()

    def start(self, adapter, agent=False):
        if adapter not in ('codex', 'claude', 'cursor', 'device'):
            raise SetupError('invalid_request')
        with self.lock:
            if self.backend.job and self.backend.job.get('state') == 'running':
                raise SetupError('job_busy')
            # Reopening while work is in flight joins it, instead of installing twice.
            if self.value is None:
                self.value = {'schema': 1, 'id': uuid.uuid4().hex, 'grant': secrets.token_urlsafe(32),
                              'adapter': adapter, 'character': 'auto', 'speech_model': 'auto', 'agent_at': 0,
                              'steps': {name: {'state': 'pending', 'code': 'pending', 'at': time.time()} for name in STEPS}}
                if adapter != 'codex':
                    self.value['steps']['pet'].update(state='unsupported', code='pet_unsupported')
            elif self.value['adapter'] != adapter:
                raise SetupError('setup_adapter_conflict')
            elif not (self.worker and self.worker.is_alive()):
                for name in STEPS[:-1]:
                    if self.value['steps'][name]['state'] != 'skipped':
                        self.value['steps'][name].update(state='pending', code='checking')
            if agent:
                self.value['agent_at'] = time.time()
                if self.value['steps']['pet']['state'] == 'ready':
                    # Account ownership/activation must be rechecked by this host agent.
                    self.value['steps']['pet'].update(state='pending', code='agent_required')
            self.save()
            self.kick()
            return self.public()

    def kick(self):
        with self.lock:
            if self.worker and self.worker.is_alive():
                return
            if any(self.value['steps'][k]['state'] == 'pending' for k in STEPS[:-1]):
                self.worker = threading.Thread(target=self.run, daemon=True)
                self.worker.start()

    def run(self):
        for name in STEPS[:-1]:
            with self.lock:
                if self.value['steps'][name]['state'] != 'pending':
                    continue
                if name != 'environment' and self.value['steps']['environment']['state'] != 'ready':
                    self.set_step(name, 'failed', 'dependencies_missing')
                    continue
                self.set_step(name, 'running', 'checking')
            try:
                self.backend.configure_feature(name, lambda code: self.set_step(name, 'running', code))
                self.set_step(name, 'ready', 'ready')
            except SetupError as error:
                code = str(error) if str(error) in CODES else 'operation_failed'
                self.set_step(name, 'unsupported' if code == 'autostart_unsupported' else 'failed', code)
            except Exception:
                self.set_step(name, 'failed', 'operation_failed')

    def edit(self, action, name=None, character=None):
        with self.lock:
            if self.value is None:
                raise SetupError('setup_not_started')
            if action not in ('character', 'speech_model') and name != 'pet' and self.worker and self.worker.is_alive():
                raise SetupError('job_busy')
            if action == 'speech_model':
                if self.value['steps']['speech']['state'] == 'running':
                    raise SetupError('job_busy')
                if not isinstance(character,str) or character not in ('auto', 'tiny', 'base', 'small'):
                    raise SetupError('invalid_request')
                self.value['speech_model'] = character
                self.set_step('speech', 'pending', 'pending')
                self.kick()
                return self.public()
            if action == 'character':
                if self.value['adapter'] != 'codex' or not isinstance(character,str) or character not in {'auto', *[c['id'] for c in self.backend.pet_catalog()]}:
                    raise SetupError('invalid_request')
                if self.value['steps']['pet']['state'] == 'running':
                    raise SetupError('job_busy')
                self.value['character'] = character
                self.set_step('pet', 'pending', 'agent_required')
            else:
                if name not in STEPS or action not in ('retry', 'skip'):
                    raise SetupError('invalid_request')
                if self.value['steps'][name]['state'] == 'running':
                    raise SetupError('job_busy')
                if name in ('environment', 'connection') and action == 'skip':
                    raise SetupError('invalid_request')
                if name == 'pet' and self.value['adapter'] != 'codex':
                    raise SetupError('invalid_request')
                self.set_step(name, 'pending' if action == 'retry' else 'skipped', 'pending' if action == 'retry' else 'user_skipped')
                if name == 'environment' and action == 'retry':
                    for dependent in STEPS[1:-1]:
                        if self.value['steps'][dependent]['code'] == 'dependencies_missing':
                            self.set_step(dependent, 'pending', 'pending')
            self.save()
            self.kick()
            return self.public()

    def agent(self, data):
        operation = data.get('operation')
        if operation == 'attach' and set(data) == {'operation', 'adapter'}:
            self.start(data['adapter'], agent=True)
            with self.lock:
                return {'run': self.value['id'], 'grant': self.value['grant'], 'status': self.public()}
        with self.lock:
            if (self.value is None or data.get('run') != self.value['id'] or
                not isinstance(data.get('grant'), str) or not data['grant'].isascii() or
                not secrets.compare_digest(data['grant'], self.value['grant'])):
                raise SetupError('setup_agent_expired')
            if operation == 'status' and set(data) == {'operation', 'run', 'grant'}:
                self.value['agent_at'] = time.time()
                return self.public()
            if operation != 'pet' or set(data) != {'operation', 'run', 'grant', 'state', 'character'}:
                raise SetupError('invalid_request')
            if self.value['adapter'] != 'codex':
                raise SetupError('invalid_request')
            state, character = data['state'], data['character']
            if not isinstance(character,str) or character not in {'auto', *[c['id'] for c in self.backend.pet_catalog()]}:
                raise SetupError('invalid_request')
            current = self.value['steps']['pet']['state']
            outcomes = {'working': ('running', 'pet_working'), 'verified': ('ready', 'pet_verified'),
                        'needs_auth': ('needs_user', 'pet_auth_required'), 'failed': ('failed', 'pet_failed')}
            if not isinstance(state,str) or state not in outcomes or current == 'skipped' or (state == 'verified' and current != 'running'):
                raise SetupError('invalid_request')
            if self.value['character'] not in ('auto', character):
                raise SetupError('setup_pet_changed')
            self.value['agent_at'] = time.time()
            self.set_step('pet', *outcomes[state])
            if state == 'verified':
                self.value['steps']['pet']['character'] = character
                self.save()
            return self.public()
