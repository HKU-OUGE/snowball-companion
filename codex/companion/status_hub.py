#!/usr/bin/env python3
"""Local status and posted-question hub; reminder actions never answer or approve."""
import argparse
import hashlib
import fcntl
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import tempfile
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from attention_model import projection, question_rows, valid_questions, SNOOZE_SECONDS, QID, clean_question
from codex_desktop import CodexDesktop, DeliveryError

VERSION = 1
PROVIDERS = ('Codex', 'Claude', 'Cursor')
PHASES = ('idle', 'thinking', 'running', 'waiting', 'completed', 'failed')
TOKEN_PATH = Path.home() / '.config/xueqiu/hub-token'
STATE_DIR = Path.home() / '.local/state/xueqiu/hub'
ID = re.compile(r'^[A-Za-z0-9_.:-]+$')
MAX_BODY = 65536
MAX_SESSIONS = 128
MAX_AGE = 120
FUTURE_SKEW = 5
PET_IDS = frozenset(('snowball', 'border-collie', 'orange-cat', 'ragdoll',
    'chiikawa', 'hachiware', 'usagi', 'my-melody', 'kuromi', 'homelander',
    'joseph-joestar', 'stellalou', 'frank-underwood', 'heisenberg'))


class Invalid(ValueError):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def atomic_json(path, data):
    """Readers see either complete previous JSON or complete next JSON, mode 0600."""
    path = Path(path)
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_token(path=TOKEN_PATH):
    path = Path(path)
    private_dir(path.parent)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        pass
    else:
        with os.fdopen(fd, 'w') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
    if path.is_symlink() or not path.is_file():
        raise ValueError('Token must be a regular private file')
    os.chmod(path, 0o600)
    token = path.read_text().strip()
    if len(token) < 32 or len(token) > 128 or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
        raise ValueError('Invalid token file; do not print its contents')
    return token


def identifier(value, maximum):
    return isinstance(value, str) and 1 <= len(value) <= maximum and ID.fullmatch(value)


def strict_json(raw):
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise Invalid('duplicate_json_key')
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(Invalid('invalid_number')))
    except (ValueError, UnicodeError):
        raise Invalid('invalid_json') from None


def validate_event(event, now):
    required = {'version', 'provider', 'source', 'session_id', 'event_id', 'seq', 'timestamp', 'kind'}
    allowed = required | {'phase', 'ttl_seconds', 'display_name', 'pending_input', 'questions', 'activity_phase'}
    if not isinstance(event, dict) or not required <= event.keys() or event.keys() - allowed:
        raise Invalid('invalid_fields')
    if type(event['version']) is not int or event['version'] != VERSION:
        raise Invalid('invalid_version')
    if event['provider'] not in PROVIDERS or event['kind'] not in ('task', 'heartbeat'):
        raise Invalid('invalid_enum')
    for key, limit in [('source', 64), ('session_id', 96), ('event_id', 96)]:
        if not identifier(event[key], limit):
            raise Invalid('invalid_identifier')
    if type(event['seq']) is not int or not 1 <= event['seq'] <= 2**53 - 1:
        raise Invalid('invalid_sequence')
    stamp = event['timestamp']
    if type(stamp) not in (int, float) or not math.isfinite(stamp):
        raise Invalid('invalid_timestamp')
    if stamp < now - MAX_AGE or stamp > now + FUTURE_SKEW:
        raise Invalid('expired_or_future_event', 409)
    ttl = event.get('ttl_seconds', 30)
    if type(ttl) is not int or not 5 <= ttl <= 300:
        raise Invalid('invalid_ttl')
    if 'display_name' in event:
        title = event['display_name']
        if (not isinstance(title, str) or len(title) > 64
                or any(unicodedata.category(c)[0] == 'C' for c in title)):
            raise Invalid('invalid_display_name')
        if len(title.encode('utf-8')) > 192:
            raise Invalid('invalid_display_name')
    if 'pending_input' in event:
        count = event['pending_input']
        if type(count) is not int or not 0 <= count <= 4096 or event['kind'] != 'task':
            raise Invalid('invalid_pending_input')
        if count and event.get('phase') != 'waiting':
            raise Invalid('pending_input_requires_waiting')
    if 'questions' in event:
        if event['kind'] != 'task' or not valid_questions(event['questions']) or len(event['questions']) > event.get('pending_input', 0):
            raise Invalid('invalid_questions')
    if 'activity_phase' in event and event['activity_phase'] not in PHASES:
        raise Invalid('invalid_activity_phase')
    if event['kind'] == 'task':
        if event.get('phase') not in PHASES:
            raise Invalid('invalid_phase')
    elif 'phase' in event:
        raise Invalid('heartbeat_cannot_set_phase')


class StatusHub:
    def __init__(self, state_dir=STATE_DIR, clock=time.time, desktop=None):
        self.state_dir = private_dir(state_dir)
        self.clock = clock
        self.desktop = desktop
        self.answer_path = self.state_dir / 'answers.json'
        self.answers = json.loads(self.answer_path.read_text()) if self.answer_path.exists() else {}
        self.message_path=self.state_dir/'messages.json'
        self.messages=json.loads(self.message_path.read_text()) if self.message_path.exists() else {}
        for receipt in self.messages.values():
            if receipt.get('status')=='sending':receipt['status']='unconfirmed'
        # An interrupted delivery is unknown, never automatically replayed.
        for receipt in self.answers.values():
            if receipt.get('status') == 'sending': receipt['status'] = 'unconfirmed'
        self.lock = threading.RLock()
        self.records = {}
        self.focus = None
        self.pinned = False
        self.focus_provider = None
        self.priority_order = []
        self.started_at = clock()
        self.snapshot_path = self.state_dir / 'snapshot.json'
        self.store_path = self.state_dir / 'sessions.json'
        self.history_dir = self.state_dir / 'session-history'
        self.pet_path = self.state_dir / 'pet.json'
        self.pet = {'character_id': 'snowball', 'revision': 0}
        if self.pet_path.exists():
            pet = json.loads(self.pet_path.read_text())
            if (not isinstance(pet, dict) or pet.get('character_id') not in PET_IDS
                    or type(pet.get('revision')) is not int or not 0 <= pet['revision'] < 2**53):
                raise ValueError('Invalid local pet selection; preserve file before repair')
            self.pet = {key: pet[key] for key in ('character_id', 'revision')}
        if self.store_path.exists():
            saved = json.loads(self.store_path.read_text())
            if saved.get('version') != VERSION or not isinstance(saved.get('records'), dict):
                raise ValueError('Invalid local hub state; preserve file before repair')
            self.records = saved['records']
            self.focus, self.pinned = saved.get('focus'), saved.get('pinned', False)
            self.focus_provider = saved.get('focus_provider')
            # Old row taps created implicit pins. Only the new explicit settings
            # policy may retain an active pin across upgrades.
            if saved.get('focus_policy_version') != 1:
                self.pinned = False
            order = saved.get('priority_order', [])
            # Dormant sessions retain their positions when their live records
            # move to disk. A later task resumes at the user's original rank.
            self.priority_order = list(dict.fromkeys(k for k in order if isinstance(k, str) and
                (k in self.records or self.history_path(k).is_file()))) if isinstance(order, list) else []
            self.priority_order.extend(k for k in self.records if k not in self.priority_order)
        self.publish()

    @staticmethod
    def key(source, session_id):
        return source + '/' + session_id

    def persist(self):
        atomic_json(self.store_path, {'version': VERSION, 'records': self.records,
                                     'focus': self.focus, 'pinned': self.pinned, 'focus_provider': self.focus_provider,
                                     'priority_order': self.priority_order, 'focus_policy_version': 1})

    def history_path(self, key):
        return self.history_dir / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def historical_record(self, key):
        path = self.history_path(key)
        if not path.exists():
            return None
        # History is private local state, never a new activity observation.
        if path.is_symlink() or path.stat().st_size > 262144:
            raise Invalid('session_history_invalid', 503)
        try:
            value = json.loads(path.read_text())
            if (value.get('version') != VERSION or value.get('key') != key or
                    not isinstance(value.get('record'), dict)):
                raise ValueError('Invalid history')
            return value['record']
        except (OSError, ValueError, AttributeError):
            raise Invalid('session_history_invalid', 503) from None

    def make_room(self, now):
        """Retire only expired, quiet terminal records, never unfinished work.

        Save before evicting, retain priority and replay guards, and restore only
        on a newly accepted source event. No chat, question or task is deleted.
        """
        if sum(not r.get('catalog_only',False) for r in self.records.values()) < MAX_SESSIONS:
            return
        candidates = [(record['updated_at'], key) for key, record in self.records.items()
            if not record.get('catalog_only') and record['expires_at'] <= now and (record.get('task_expires_at') or 0) <= now
            and record.get('phase') in ('idle', 'completed', None)
            and not record.get('pending_input') and not record.get('questions')
            and not (self.pinned and key == self.focus)]
        if not candidates:
            raise Invalid('session_capacity_reached', 409)
        _, key = min(candidates)
        atomic_json(self.history_path(key), {'version': VERSION, 'key': key, 'record': self.records[key]})
        del self.records[key]

    def accept(self, event):
        with self.lock:
            now = self.clock()
            validate_event(event, now)
            key = self.key(event['source'], event['session_id'])
            old = self.records.get(key)
            if old is None:
                old = self.historical_record(key)
            digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if old:
                previous = old['recent'].get(event['event_id'])
                if previous:
                    if previous != digest:
                        raise Invalid('event_id_conflict', 409)
                    return {'ok': True, 'duplicate': True, 'seq': old['seq']}
                if event['provider'] != old['provider']:
                    raise Invalid('provider_conflict', 409)
                if event['seq'] <= old['seq'] or event['timestamp'] < old['event_timestamp']:
                    raise Invalid('out_of_order', 409)
            if key not in self.records or self.records[key].get('catalog_only'):
                self.make_room(now)
            record = dict(old or {'provider': event['provider'], 'source': event['source'],
                                 'session_id': event['session_id'], 'phase': None,
                                 'phase_at': None, 'task_event_at': None, 'task_expires_at': None, 'recent': {}})
            # A source heartbeat reports observation/liveness, never task completion.
            record.update(seq=event['seq'], event_timestamp=event['timestamp'],
                          updated_at=now, ttl_seconds=event.get('ttl_seconds', 30))
            # TTL starts at event creation, so delayed delivery cannot extend stale evidence.
            record['expires_at'] = min(now, event['timestamp']) + record['ttl_seconds']
            if 'display_name' in event:
                record['display_name'] = event['display_name']
            if event['kind'] == 'task':
                if 'pending_input' in event:
                    record['pending_input'] = event['pending_input']
                record['phase'] = 'waiting' if record.get('pending_input', 0) else event['phase']
                record['activity_phase'] = event.get('activity_phase', event['phase'])
                if 'questions' in event:
                    record['questions'] = event['questions']
                    ids = {q['id'] for q in event['questions']}
                    record['reminders'] = {k: v for k, v in record.get('reminders', {}).items() if k in ids}
                elif not record.get('pending_input', 0):
                    record['questions'] = []
                    record['reminders'] = {}
                record['phase_at'] = event['timestamp']
                record['task_event_at'] = now
                record['task_expires_at'] = record['expires_at']
                if not self.pinned and (self.focus_provider is None or event['provider'] == self.focus_provider):
                    self.focus = key
            record['recent'] = dict(record['recent'])
            record['recent'][event['event_id']] = digest
            record['recent'] = dict(list(record['recent'].items())[-128:])
            record.pop('catalog_only',None)
            self.records[key] = record
            if key not in self.priority_order:
                self.priority_order.append(key)
            self.persist()
            self.publish()
            return {'ok': True, 'duplicate': False, 'seq': event['seq']}

    def sync_directory(self):
        """Catalog rows do not synthesize task events or renew activity leases."""
        directory=getattr(self.desktop,'directory',None)
        if not isinstance(getattr(directory,'rows',None),dict) or directory.fresh() is not True:return
        with self.lock:
            changed=False
            for key,row in directory.rows.items():
                if key not in self.records:
                    old=self.historical_record(key)
                    if old is None:
                        old={'provider':'Codex','source':'codex-hooks','session_id':key.split('/')[1],
                            'phase':None,'phase_at':None,'task_event_at':None,'task_expires_at':None,'recent':{},
                            'seq':0,'event_timestamp':0,'updated_at':0,'expires_at':0,'ttl_seconds':30}
                    old['catalog_only']=True;self.records[key]=old;changed=True
                if self.records[key].get('display_name')!=row['display_name']:
                    self.records[key]['display_name']=row['display_name'];changed=True
                if key not in self.priority_order:self.priority_order.append(key);changed=True
            if changed:self.persist()

    def create_session(self,request):
        """Create an independent Codex chat only after explicit text confirmation."""
        if (not isinstance(request,dict) or set(request)!={'text','request_id'} or
            not isinstance(request['request_id'],str) or not QID.fullmatch(request['request_id']) or
            not isinstance(request['text'],str) or not request['text'].strip() or len(request['text'])>1000 or
            any(unicodedata.category(c)[0]=='C' and c not in '\n\t' for c in request['text'])):raise Invalid('invalid_message')
        text=request['text'].strip();rid=request['request_id'];digest=hashlib.sha256(('create-projectless\0'+text).encode()).hexdigest()
        with self.lock:
            prior=self.messages.get(rid)
            if prior:
                if prior['digest']!=digest:raise Invalid('message_id_reused',409)
                return {'delivery':prior['status'],'creation_pending':prior.get('creation_pending',False)}
            if not self.desktop or not self.desktop.available() or not self.desktop.app_tools.available():raise Invalid('message_unavailable',409)
            self.messages[rid]={'digest':digest,'status':'sending','at':self.clock(),'kind':'create'}
            self.messages=dict(list(self.messages.items())[-256:]);atomic_json(self.message_path,self.messages)
        result={}
        try:result=self.desktop.create_instruction(text,rid);status='accepted'
        except DeliveryError as exc:status='unconfirmed' if exc.uncertain else 'rejected'
        except (OSError,ValueError,TypeError,KeyError):status='unconfirmed'
        with self.lock:
            self.messages[rid].update(status=status,creation_pending=bool(result.get('creation_pending')))
            if result.get('thread_id'):self.messages[rid]['thread_id']=result['thread_id']
            atomic_json(self.message_path,self.messages)
        return {'delivery':status,'creation_pending':bool(result.get('creation_pending'))}

    def set_attention(self, request):
        with self.lock:
            if (not isinstance(request, dict) or set(request) != {'session', 'question', 'action'} or
                    not isinstance(request['session'], str) or not isinstance(request['question'], str) or
                    not QID.fullmatch(request['question']) or request['action'] not in ('snooze', 'ignore', 'restore')):
                raise Invalid('invalid_attention_action')
            record = self.records.get(request['session'])
            if not record or request['question'] not in {q['id'] for q in record.get('questions', [])}:
                raise Invalid('question_no_longer_pending', 409)
            reminders = dict(record.get('reminders', {}))
            old = reminders.get(request['question'], {})
            action = request['action']
            if action == 'restore':
                reminders.pop(request['question'], None)
            elif action == 'ignore':
                reminders[request['question']] = {'mode': 'ignore', 'until': 0}
            elif old.get('mode') != 'snooze' or old.get('until', 0) <= self.clock():
                reminders[request['question']] = {'mode': 'snooze', 'until': self.clock() + SNOOZE_SECONDS}
            record['reminders'] = reminders
            self.persist()
            return self.publish()

    def answer(self, request):
        required = {'session', 'question', 'request_id'}
        if (not isinstance(request, dict) or not required <= request.keys() or
                set(request)-required not in ({'choice'}, {'text'}) or
                not all(isinstance(request[k], str) for k in required) or
                not QID.fullmatch(request['question']) or not QID.fullmatch(request['request_id'])):
            raise Invalid('invalid_answer')
        identity = request['session']+'/'+request['question']
        if 'choice' in request:
            if type(request['choice']) is not int or not 0<=request['choice']<6: raise Invalid('invalid_choice')
            selection={'choice':request['choice']}
        else:
            if not isinstance(request['text'],str) or not request['text'].strip() or len(request['text'])>1000: raise Invalid('invalid_answer_text')
            selection={'text':request['text'].strip()}
        selection_digest=hashlib.sha256(json.dumps(selection,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            prior = self.answers.get(identity)
            if prior and prior['status'] in ('accepted','sending','unconfirmed'):
                if prior.get('selection_digest')!=selection_digest: raise Invalid('answer_already_submitted',409)
                return {'delivery':prior['status'], 'snapshot':self.snapshot()}
            record = self.records.get(request['session'])
            question = next((q for q in (record or {}).get('questions', []) if q['id']==request['question']), None)
            if not question: raise Invalid('question_no_longer_pending', 409)
            if not self.desktop or not self.desktop.available(): raise Invalid('answer_unavailable', 503)
            route = self.desktop.route(request['session'], request['question'])
            if not route or clean_question(route.get('question')) != question['text'] or route['options'] != question.get('options', []): raise Invalid('answer_unavailable', 409)
            if 'choice' in request:
                index = request['choice']
                if type(index) is not int or not 0 <= index < len(route['options']): raise Invalid('invalid_choice')
                answer = route['options'][index]
            else:
                answer = request['text']
                if not isinstance(answer, str) or not answer.strip() or len(answer)>1000 or any(unicodedata.category(c)[0]=='C' and c not in '\n\t' for c in answer): raise Invalid('invalid_answer_text')
                answer = answer.strip()
            # Persist identity/outcome, never answer text. One pending delivery per question.
            self.answers[identity] = {'request_id':request['request_id'], 'status':'sending', 'at':self.clock(), 'selection_digest':selection_digest}
            self.answers = dict(list(self.answers.items())[-256:])
            atomic_json(self.answer_path,self.answers)
        # Do not hold the Hub lock across IPC: real UserPromptSubmit hooks call back here.
        try:
            self.desktop.deliver(request['session'],request['question'],answer,request['request_id'])
            status, reason = 'accepted', ''
        except DeliveryError as error:
            status = 'unconfirmed' if error.uncertain else 'rejected'
            reason = error.code.split(':',1)[0]
        except (OSError, ValueError, KeyError, TypeError):
            status, reason = 'unconfirmed', 'delivery_unconfirmed'
        with self.lock:
            self.answers[identity]['status'] = status
            self.answers[identity]['reason'] = reason
            atomic_json(self.answer_path,self.answers)
            return {'delivery':status,'snapshot':self.publish()}

    def message(self,request):
        """An explicit confirmed instruction to an exact existing chat."""
        if (not isinstance(request,dict) or set(request)!={'session','text','request_id'} or
            not isinstance(request['session'],str) or not isinstance(request['request_id'],str) or
            not QID.fullmatch(request['request_id']) or not isinstance(request['text'],str) or
            not request['text'].strip() or len(request['text'])>1000 or
            any(unicodedata.category(c)[0]=='C' and c not in '\n\t' for c in request['text'])):raise Invalid('invalid_message')
        text=request['text'].strip();rid=request['request_id']
        digest=hashlib.sha256((request['session']+'\0'+text).encode()).hexdigest()
        with self.lock:
            prior=self.messages.get(rid)
            if prior:
                if prior['digest']!=digest:raise Invalid('message_id_reused',409)
                return {'delivery':prior['status']}
            record=self.records.get(request['session'])
            if not record or record['provider']!='Codex' or not self.desktop or not self.desktop.available() or not self.desktop.instruction_route(request['session']):raise Invalid('message_unavailable',409)
            self.messages[rid]={'digest':digest,'status':'sending','at':self.clock()}
            self.messages=dict(list(self.messages.items())[-256:]);atomic_json(self.message_path,self.messages)
        try:
            self.desktop.deliver_instruction(request['session'],text,rid);status='accepted'
        except DeliveryError as exc:status='unconfirmed' if exc.uncertain else 'rejected'
        except (OSError,ValueError,TypeError,KeyError):status='unconfirmed'
        with self.lock:
            self.messages[rid]['status']=status;atomic_json(self.message_path,self.messages)
        return {'delivery':status}

    def set_pet(self, request):
        """Persist desired character, not a claim of device/cloud application."""
        with self.lock:
            if (not isinstance(request, dict) or set(request) != {'character_id'}
                    or not isinstance(request['character_id'], str)
                    or request['character_id'] not in PET_IDS):
                raise Invalid('invalid_character')
            if self.pet['revision'] == 0 or request['character_id'] != self.pet['character_id']:
                updated = {'character_id': request['character_id'], 'revision': self.pet['revision'] + 1}
                atomic_json(self.pet_path, updated)
                self.pet = updated
            self.publish()
            return {'ok': True, **self.pet, 'device_applied': False}

    def set_focus(self, request):
        with self.lock:
            if isinstance(request, dict) and set(request) == {'provider', 'order'}:
                provider, order = request['provider'], request['order']
                if (provider not in PROVIDERS or not isinstance(order, list) or not 1 <= len(order) <= MAX_SESSIONS or
                        any(not isinstance(k, str) or k not in self.records or self.records[k]['provider'] != provider for k in order) or
                        len(set(order)) != len(order)):
                    raise Invalid('invalid_priority_order')
                # Replace selected slots under one lock and persist once. Other
                # providers and sessions outside the displayed page keep their slots.
                selected = set(order)
                replacement = iter(order)
                self.priority_order = [next(replacement) if k in selected else k for k in self.priority_order]
                self.pinned = False
                self.focus_provider = provider
                self.focus = self.auto_focus()
            elif isinstance(request, dict) and set(request) == {'provider', 'move', 'before'}:
                provider, key, before = request['provider'], request['move'], request['before']
                if (provider not in PROVIDERS or not isinstance(key, str) or key not in self.records or
                        self.records[key]['provider'] != provider or
                        (before is not None and (not isinstance(before, str) or before not in self.records or
                                                 self.records[before]['provider'] != provider))):
                    raise Invalid('invalid_priority_move')
                if key != before:
                    self.priority_order.remove(key)
                    at = self.priority_order.index(before) if before is not None else len(self.priority_order)
                    self.priority_order.insert(at, key)
                self.pinned = False
                self.focus_provider = provider
                self.focus = self.auto_focus()
            elif isinstance(request, dict) and request.get('auto') is True and set(request) <= {'auto', 'provider'}:
                provider = request.get('provider')
                if provider is not None and provider not in PROVIDERS:
                    raise Invalid('invalid_provider')
                self.pinned = False
                self.focus_provider = provider
                self.focus = self.auto_focus()
            else:
                if not isinstance(request, dict) or set(request) != {'source', 'session_id'}:
                    raise Invalid('invalid_focus')
                if not identifier(request['source'], 64) or not identifier(request['session_id'], 96):
                    raise Invalid('invalid_focus')
                key = self.key(request['source'], request['session_id'])
                if key not in self.records:
                    raise Invalid('unknown_session', 404)
                record = self.records[key]
                phase, pending, _, _ = projection(record, self.clock())
                if not pending and (
                        phase not in ('thinking', 'running', 'waiting', 'failed') or
                        min(record['expires_at'], record.get('task_expires_at') or 0) <= self.clock()):
                    raise Invalid('session_not_active', 409)
                self.focus, self.pinned = key, True
                self.focus_provider = record['provider']
            self.persist()
            return self.publish()

    def auto_focus(self):
        now = self.clock()
        candidates = []
        for index, key in enumerate(self.priority_order):
            record = self.records.get(key)
            if record is None:
                continue
            directory=getattr(self.desktop,'directory',None)
            if isinstance(getattr(directory,'rows',None),dict) and directory.fresh() is True and record['source']=='codex-hooks' and key not in directory.rows:continue
            if record.get('catalog_only') or record['task_event_at'] is None or (self.focus_provider is not None and record['provider'] != self.focus_provider):
                continue
            fresh = min(record['expires_at'], record.get('task_expires_at') or 0) > now
            phase, pending, _, _ = projection(record, now)
            # Only confirmed current evidence can preempt; durable pending input
            # remains attention even after the ordinary activity lease expires.
            rank = (0 if fresh and phase == 'failed' else
                    1 if pending or (fresh and phase == 'waiting') else
                    2 if fresh and phase in ('thinking', 'running') else
                    3 if fresh else 4)
            tie = index if rank < 3 else -record['task_event_at']
            candidates.append((rank, tie, key))
        return min(candidates)[2] if candidates else None

    def snapshot(self):
        with self.lock:
            now = self.clock()
            released = False
            if self.pinned:
                target = self.records.get(self.focus)
                phase, pending, _, _ = projection(target, now) if target else (None, 0, 0, [])
                actionable = target and (pending > 0 or (
                    phase in ('thinking', 'running', 'waiting', 'failed') and
                    min(target.get('expires_at', 0), target.get('task_expires_at') or 0) > now))
                if not actionable:
                    self.pinned = False
                    released = True
            if not self.pinned:
                self.focus = self.auto_focus()
            if released:
                self.persist()
            result = {'version': VERSION, 'generated_at': now, 'hub_alive': True,
                      'hub_started_at': self.started_at, 'focused_session': self.focus,
                      'focus_pinned': self.pinned, 'focus_provider': self.focus_provider, 'source_fresh': False, 'status': 'unknown',
                      'bridge_alive': None, 'radio_connected': None, 'sessions': [],
                      'priority_order': [k for k in self.priority_order if k in self.records], 'pet': dict(self.pet)}
            directory=getattr(self.desktop,'directory',None)
            directory_rows=directory.rows if isinstance(getattr(directory,'rows',None),dict) and directory.fresh() is True else None
            result['directory_available']=directory_rows is not None
            result['can_create']=bool(directory_rows and self.desktop.available() and self.desktop.app_tools.available())
            for key, record in self.records.items():
                if record['source']=='codex-hooks' and directory_rows is not None and key not in directory_rows:continue
                fresh = now < record['expires_at'] and now >= record['updated_at'] - FUTURE_SKEW
                entry = {field: record[field] for field in (
                    'provider', 'source', 'session_id', 'phase', 'phase_at', 'updated_at',
                    'ttl_seconds', 'expires_at', 'seq')}
                entry['priority'] = self.priority_order.index(key)
                entry['phase'], entry['pending_input'], entry['pending_total'], entry['questions'] = projection(record, now)
                entry['summary'] = dict(self.desktop.summaries.get(key, {})) if self.desktop else {}
                for q in entry['questions']:
                    receipt = self.answers.get(key+'/'+q['id'], {})
                    q['answer_state'] = receipt.get('status', '')
                    route = self.desktop.route(key,q['id']) if self.desktop else None
                    q['answerable'] = bool(self.desktop and self.desktop.available() and route and
                        route.get('options')==q.get('options',[]) and clean_question(route.get('question'))==q['text'] and
                        q['answer_state'] not in ('accepted','sending','unconfirmed'))
                entry['display_name'] = record.get('display_name', '')
                entry['task_updated_at'] = record['task_event_at']
                entry['source_last_seen'] = record['updated_at']
                entry['source_fresh'] = fresh
                task_expiry = record.get('task_expires_at')
                entry['task_fresh'] = task_expiry is not None and now < task_expiry
                entry['task_expires_at'] = task_expiry
                entry['sidebar_visible']=directory_rows is not None and key in directory_rows
                entry['instruction_available']=bool(entry['sidebar_visible'] and self.desktop.available())
                if entry['sidebar_visible'] and entry['phase'] is None:entry['phase']='idle'
                result['sessions'].append(entry)
                if key == self.focus:
                    result.update({k: entry[k] for k in ('provider', 'source', 'session_id',
                        'updated_at', 'ttl_seconds', 'expires_at', 'seq', 'source_fresh',
                        'task_updated_at', 'source_last_seen', 'task_fresh', 'task_expires_at', 'display_name', 'pending_input', 'pending_total', 'questions')})
                    result['last_state'] = entry['phase']
                    result['phase_at'] = record['phase_at']
                    result['terminal'] = entry['phase'] in ('completed', 'failed')
                    if entry['pending_input']:
                        # An unresolved question is durable attention, not proof
                        # of current AI activity. Preserve both freshness flags.
                        result['state'] = 'waiting'
                        result['status'] = 'attention'
                    elif entry['pending_total'] and entry['phase'] == 'idle':
                        # Muted pending questions are quiet, not evidence of AI activity.
                        result['state'] = 'idle'
                        result['status'] = 'quiet_attention'
                    elif fresh and entry['task_fresh'] and record['phase'] is not None:
                        result['state'] = entry['phase']
                        result['status'] = 'known'
            # A live hub with no outstanding work is a positive idle observation.
            # Expired running/waiting/failed records remain unknown, not invented idle.
            relevant = [r for r in result['sessions'] if self.focus_provider is None or r['provider'] == self.focus_provider]
            if result['status'] == 'unknown' and all(not r['pending_input'] and r['phase'] in ('idle', 'completed', None) for r in relevant):
                result.update(status='ready', state='idle', provider=self.focus_provider or result.get('provider', 'Codex'), pending_input=0)
            return result

    def publish(self):
        with self.lock:
            result = self.snapshot()
            atomic_json(self.snapshot_path, result)
            return result


class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, hub, token):
        if address[0] != '127.0.0.1':
            raise ValueError('Hub must bind to 127.0.0.1')
        self.hub, self.token = hub, token
        super().__init__(address, HubHandler)


class HubHandler(BaseHTTPRequestHandler):
    server_version = 'XueqiuHub/1'
    def log_message(self, *args):
        pass  # Never log headers or incoming content.

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def reply(self, status, data):
        payload = json.dumps(data, separators=(',', ':'), allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True

    def authorize(self):
        expected = 'Bearer ' + self.server.token
        auth = self.headers.get_all('Authorization', [])
        if len(auth) != 1 or not hmac.compare_digest(auth[0].encode(), expected.encode()):
            raise Invalid('unauthorized', 401)
        # No browser cookie auth, no CORS, and no website origins on this local API.
        if self.headers.get('Origin'):
            raise Invalid('browser_origin_not_allowed', 403)
        hosts = self.headers.get_all('Host', [])
        allowed = {'127.0.0.1', 'localhost', f'127.0.0.1:{self.server.server_port}',
                   f'localhost:{self.server.server_port}'}
        if len(hosts) != 1 or hosts[0] not in allowed:
            raise Invalid('invalid_host', 403)

    def do_GET(self):
        try:
            self.authorize()
            if self.path not in ('/v1/snapshot', '/v1/health'):
                raise Invalid('not_found', 404)
            data = self.server.hub.publish() if self.path == '/v1/snapshot' else {
                'ok': True, 'version': VERSION, 'hub_alive': True,
                'bridge_alive': None, 'radio_connected': None}
            self.reply(200, data)
        except Invalid as error:
            self.reply(error.status, {'error': error.code})

    def do_POST(self):
        try:
            self.authorize()
            if self.path not in ('/v1/events', '/v1/focus', '/v1/pet', '/v1/attention', '/v1/answer','/v1/message','/v1/session/create'):
                raise Invalid('not_found', 404)
            if self.headers.get('Transfer-Encoding'):
                raise Invalid('transfer_encoding_not_supported')
            sizes = self.headers.get_all('Content-Length', [])
            if len(sizes) != 1 or not re.fullmatch(r'[0-9]+', sizes[0]):
                raise Invalid('content_length_required', 411)
            size = int(sizes[0])
            if size < 2 or size > MAX_BODY:
                raise Invalid('body_too_large', 413)
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                raise Invalid('application_json_required', 415)
            raw = self.rfile.read(size)
            if len(raw) != size:
                raise Invalid('incomplete_body')
            data = strict_json(raw)
            handler = {'/v1/events': self.server.hub.accept,
                       '/v1/focus': self.server.hub.set_focus,
                       '/v1/pet': self.server.hub.set_pet,
                       '/v1/attention': self.server.hub.set_attention,
                       '/v1/answer': self.server.hub.answer,
                       '/v1/message': self.server.hub.message,
                       '/v1/session/create':self.server.hub.create_session}[self.path]
            result = handler(data)
            self.reply(200, result)
        except Invalid as error:
            self.reply(error.status, {'error': error.code})
        except (TimeoutError, ConnectionError):
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4190)
    parser.add_argument('--state-dir', type=Path, default=STATE_DIR)
    parser.add_argument('--token-file', type=Path, default=TOKEN_PATH)
    args = parser.parse_args()
    private_dir(args.state_dir)
    lock_fd = os.open(args.state_dir / 'hub.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        parser.error('Another hub owns this state directory')
    desktop = CodexDesktop()
    hub = StatusHub(args.state_dir, desktop=desktop)
    server = HubServer(('127.0.0.1', args.port), hub, load_token(args.token_file))
    stop = threading.Event()
    def ticker():
        while not stop.wait(1):
            with hub.lock: records = {k:dict(v) for k,v in hub.records.items()}
            desktop.refresh(records)
            hub.sync_directory()
            hub.publish()
    worker = threading.Thread(target=ticker, daemon=True)
    worker.start()
    print(f'Snowball status hub listening on 127.0.0.1:{server.server_port}', flush=True)
    print(f'Private snapshot: {hub.snapshot_path}', flush=True)
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=2)
        server.server_close()
        # Explicit shutdown revokes freshness without fabricating a task result.
        snapshot = hub.snapshot()
        snapshot.update(hub_alive=False, source_fresh=False, status='unknown')
        snapshot.pop('state', None)
        atomic_json(hub.snapshot_path, snapshot)
        os.close(lock_fd)


if __name__ == '__main__':
    main()
