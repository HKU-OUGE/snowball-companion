from contextlib import closing
"""Local Codex Desktop adapter. Private IPC is version checked and fails closed.

Submits explicitly confirmed answers to exact pending async questions, or
confirmed instructions to existing user-selected chats. Never changes
permissions or model settings. New independent chats use the app tool only
after explicit user confirmation; read-only inventory never resumes a chat.
Summaries are bounded excerpts of visible assistant commentary/final messages.

An accepted reply acknowledges message delivery, not Desktop composer dismissal.
The inspected Desktop build finalizes its question composer only in its local
submit callback; the thread IPC used here exposes no equivalent UI operation.
Never resend an accepted answer to try to clear that separate interface state.
"""
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import time
import uuid
from datetime import datetime

from attention_model import clean_question, QID
from desktop_directory import DesktopDirectory
from app_tools import AppTools, AppToolError
from codex_activity import CodexActivity


def opaque(value):
    return hashlib.sha256(value.encode()).hexdigest()[:32]


class DeliveryError(ValueError):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


class DesktopIPC:
    def __init__(self, path):
        if os.name == 'nt':
            raise DeliveryError('desktop_control_unsupported_on_windows')
        path = Path(path)
        for p, is_socket in ((path.parent, False), (path, True)):
            s = p.lstat()
            if s.st_uid != os.getuid() or s.st_mode & 0o022 or (is_socket and not stat.S_ISSOCK(s.st_mode)):
                raise DeliveryError('desktop_socket_untrusted')
        self.socket = socket.socket(socket.AF_UNIX)
        self.socket.settimeout(4)
        self.socket.connect(str(path))
        self.client = 'initializing-client'
        try:
            response = self.request('initialize', {'clientType': 'snowball-companion'}, 0)
            self.client = response['result']['clientId']
        except BaseException:
            self.close()
            raise

    def close(self):
        self.socket.close()

    def send(self, msg):
        body = json.dumps(msg, ensure_ascii=False).encode()
        self.socket.sendall(struct.pack('<I', len(body)) + body)

    def receive(self):
        def read(size):
            out = bytearray()
            while len(out) < size:
                chunk = self.socket.recv(size - len(out))
                if not chunk: raise ConnectionError('desktop_disconnected')
                out.extend(chunk)
            return out
        size = struct.unpack('<I', read(4))[0]
        if not 0 < size <= 16*1024*1024: raise DeliveryError('desktop_protocol_changed')
        return json.loads(read(size))

    def request(self, method, params, version, target=None):
        rid = str(uuid.uuid4())
        msg = dict(type='request', requestId=rid, sourceClientId=self.client,
            method=method, version=version, params=params, timeoutMs=3000)
        if target: msg['targetClientId'] = target
        self.send(msg)
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            msg = self.receive()
            if msg.get('type') == 'client-discovery-request':
                self.send(dict(type='client-discovery-response', requestId=msg['requestId'], response={'canHandle': False}))
            elif msg.get('requestId') == rid and msg.get('type') == 'response':
                if msg.get('resultType') != 'success' or msg.get('method',method) != method:
                    # Only a specific negative response proves non-delivery.
                    reason=str(msg.get('error','protocol_changed'))[:160]
                    definitely_not_sent=reason in ('no-client-found','request-version-mismatch','no-handler-for-request','not-connected')
                    raise DeliveryError('desktop_rejected:'+reason,not definitely_not_sent)
                return msg
        raise TimeoutError('desktop_timeout')


def reply_envelope(route, answer):
    item = {'questionItemId': json.dumps(['request_user_input_async', route['call'], route['index']], separators=(',', ':')),
            'question': route['question'], 'answer': answer}
    return '<send_user_message_question_reply>\n' + json.dumps([item], ensure_ascii=False) + '\n</send_user_message_question_reply>'


class VisibleSummary:
    """Incremental bounded JSONL reader; no reasoning, user input or tool output."""
    def __init__(self):
        self.offset = 0
        self.identity = None
        self.turn = None
        self.value = {}
        self.partial = b''

    def read(self, path):
        path = Path(path)
        info = path.stat()
        if self.identity != info.st_ino or info.st_size < self.offset:
            self.identity, self.offset = info.st_ino, max(0, info.st_size-2*1024*1024)
            self.value, self.partial, self.turn = {}, b'', None
            skip = bool(self.offset)
        else:
            skip = False
        if info.st_size == self.offset: return self.value
        with path.open('rb') as stream:
            if info.st_size-self.offset > 2*1024*1024:
                self.offset, self.partial, skip = info.st_size-2*1024*1024, b'', True
            stream.seek(self.offset)
            if skip: stream.readline()
            data = self.partial + stream.read(2*1024*1024)
            self.offset = stream.tell()
        lines = data.split(b'\n')
        self.partial = lines.pop() if len(lines[-1]) < 512*1024 else b''
        for line in lines:
            if len(line) > 512*1024 or not any(x in line[:180] for x in (b'"response_item"', b'"turn_context"', b'"event_msg"')): continue
            try: event = json.loads(line)
            except (ValueError, UnicodeError): continue
            p = event.get('payload', {})
            if not isinstance(p, dict): continue
            if event.get('type') == 'turn_context':
                turn = p.get('turn_id')
                if turn and turn != self.turn:
                    self.turn, self.value = turn, {}
            if event.get('type') != 'response_item' or p.get('type') != 'message' or p.get('role') != 'assistant': continue
            if p.get('phase') not in ('commentary', 'final') or p.get('channel') == 'analysis': continue
            text = ' '.join(c.get('text', '') for c in p.get('content', []) if isinstance(c, dict) and c.get('type') == 'output_text')
            if not text.strip(): continue
            try: stamp = datetime.fromisoformat(event['timestamp'].replace('Z', '+00:00')).timestamp()
            except (KeyError, ValueError, TypeError): continue
            if stamp > time.time()+5: continue
            self.value = {'text': clean_question(text), 'kind': 'progress' if p['phase']=='commentary' else 'result', 'at': stamp}
        return self.value


class CodexDesktop:
    def __init__(self, home=None, ipc_factory=DesktopIPC):
        self.home = Path(home or Path.home())
        self.root = Path(os.environ.get('CODEX_HOME', str(self.home/'.codex'))) if self.home == Path.home() else self.home/'.codex'
        self.routes_root = self.home/'.local/state/xueqiu/codex-adapter/hooks'
        self.socket_path = self.root/'ipc/ipc.sock'
        self.ipc_factory = ipc_factory
        self.summaries, self.readers, self.paths = {}, {}, {}
        self.last_index = 0
        self.directory=DesktopDirectory(self.home)
        self.app_tools=AppTools(self.home)
        self.activity=CodexActivity(self.app_tools)

    def route(self, session, question):
        if not session.startswith('codex-hooks/') or not QID.fullmatch(session.split('/',1)[1]): return None
        path = self.routes_root/(session.split('/',1)[1]+'.json')
        try:
            if path.stat().st_size > 512*1024: return None
            record = json.loads(path.read_text())
            route = record.get('question_routes', {}).get(question)
            if (not route or route.get('kind') != 'async' or opaque(route['thread']) != session.split('/',1)[1] or
                    opaque(opaque(route['call'])+':'+str(route['index'])) != question or
                    route['index'] not in record.get('pending_questions', {}).get(opaque(route['call']), [])):
                return None
            return dict(route, activity=record.get('activity_phase', 'idle'))
        except (OSError, ValueError, KeyError, TypeError): return None

    def available(self):
        if os.name == 'nt':
            return False
        try:
            s = self.socket_path.lstat()
            return stat.S_ISSOCK(s.st_mode) and s.st_uid == os.getuid() and not s.st_mode & 0o022
        except OSError: return False

    def instruction_route(self, session):
        """Resolve only a currently visible, unarchived local user chat."""
        if not isinstance(session,str) or not session.startswith('codex-hooks/') or not QID.fullmatch(session.split('/',1)[1]):return None
        row=self.directory.resolve(session)
        if not row:return None
        activity='idle'
        try:
            record=json.loads((self.routes_root/(session.split('/',1)[1]+'.json')).read_text())
            if record.get('internal_session'):return None
            activity=record.get('activity_phase','idle')
        except (OSError,ValueError,TypeError):pass
        return {'thread':row['thread'],'activity':activity}

    def create_instruction(self,text,request_id):
        self.directory.refresh(force=True)
        if not self.directory.fresh() or not self.directory.rows:raise DeliveryError('desktop_unavailable')
        caller=next(iter(self.directory.rows.values()))['thread']
        try:
            # Current feature explicitly creates an independent local chat.
            # Model, permissions and reasoning remain the user's app defaults.
            self.app_tools.call('list_projects',{},caller,request_id+'-projects')
            result=self.app_tools.call('create_thread',{'prompt':text,'target':{'type':'projectless'}},caller,request_id)
        except AppToolError as exc:raise DeliveryError(exc.code,exc.uncertain) from exc
        tid=result.get('threadId') or result.get('clientThreadId')
        if not isinstance(tid,str) or not tid:raise DeliveryError('delivery_unconfirmed',True)
        self.directory.last_scan=0
        return {'status':'accepted','thread_id':tid,'creation_pending':not bool(result.get('threadId'))}

    def deliver_instruction(self, session, text, request_id):
        route=self.instruction_route(session)
        if not route:raise DeliveryError('session_unavailable')
        if self.app_tools.available():
            try:
                result=self.app_tools.call('send_message_to_thread',{'threadId':route['thread'],'hostId':'local','prompt':text},route['thread'],request_id)
            except AppToolError as exc:raise DeliveryError(exc.code,exc.uncertain) from exc
            # Success is a host acknowledgement. Never replay after a timeout.
            if result.get('status') in ('error','failed') or result.get('success') is False:raise DeliveryError('delivery_unconfirmed',True)
            return {'status':'accepted'}
        try:ipc=self.ipc_factory(self.socket_path)
        except (OSError,ValueError,KeyError) as exc:raise DeliveryError('desktop_unavailable') from exc
        try:
            try:owner=ipc.request('thread-owner-discovery',{'hostId':'local','conversationId':route['thread']},1)['handledByClientId']
            except (OSError,ValueError,KeyError) as exc:raise DeliveryError('desktop_owner_unavailable') from exc
            current=self.instruction_route(session)
            if not current or current['thread']!=route['thread']:raise DeliveryError('session_changed')
            message_id=str(uuid.uuid5(uuid.NAMESPACE_URL,'snowball-instruction:'+request_id))
            items=[{'type':'text','text':text,'text_elements':[]}]
            if current['activity'] in ('running','thinking'):
                method,version='thread-follower-steer-turn',1
                params={'conversationId':route['thread'],'input':items,'clientUserMessageId':message_id,
                    'restoreMessage':{'id':message_id,'context':{'prompt':text}}}
            else:
                method,version='thread-follower-start-turn',2
                params={'conversationId':route['thread'],'turnStart':{'request':{'threadId':route['thread'],
                    'input':items,'clientUserMessageId':message_id},'context':{'inheritThreadSettings':True}}}
            try:response=ipc.request(method,params,version,owner)
            except DeliveryError:raise
            except (OSError,TimeoutError,ValueError) as exc:raise DeliveryError('delivery_unconfirmed',True) from exc
            value=response.get('result',{}).get('result')
            if not isinstance(value,dict) or not(value.get('turnId') or value.get('turn',{}).get('id')):raise DeliveryError('delivery_unconfirmed',True)
            return {'status':'accepted'}
        finally:ipc.close()

    def refresh(self, records):
        self.directory.refresh()
        wanted = {r['session_id'] for r in records.values() if r['source']=='codex-hooks' and
            (r.get('pending_input') or min(r.get('expires_at',0),r.get('task_expires_at') or 0)>time.time())}
        wanted.update(identity.split('/')[1] for identity in self.directory.rows)
        if time.monotonic()-self.last_index > 15 or wanted - self.paths.keys():
            self.last_index = time.monotonic()
            for dbpath in sorted(self.root.glob('state_[0-9]*.sqlite'), reverse=True)[:1]:
                try:
                    with closing(sqlite3.connect(dbpath.as_uri()+'?mode=ro', uri=True, timeout=.05)) as db:
                        db.execute('PRAGMA query_only=ON')
                        for thread, path in db.execute('SELECT id,rollout_path FROM threads WHERE archived=0'):
                            sid = opaque(thread)
                            if sid in wanted and isinstance(path,str):
                                resolved = Path(path).resolve()
                                if resolved.is_relative_to(self.root/'sessions'):
                                    self.paths[sid] = resolved
                except (sqlite3.Error,OSError): pass
        for sid in wanted:
            if sid not in self.paths: continue
            try:
                value = self.readers.setdefault(sid, VisibleSummary()).read(self.paths[sid])
                self.summaries['codex-hooks/'+sid] = dict(value)
            except (OSError, ValueError): pass

    def deliver(self, session, question, answer, request_id):
        route = self.route(session, question)
        if not route: raise DeliveryError('question_no_longer_pending')
        try:
            ipc = self.ipc_factory(self.socket_path)
        except (OSError,ValueError,KeyError) as exc:
            raise DeliveryError('desktop_unavailable') from exc
        try:
            try:
                owner = ipc.request('thread-owner-discovery', {'hostId':'local','conversationId':route['thread']}, 1)['handledByClientId']
            except (OSError,ValueError,KeyError) as exc:
                raise DeliveryError('desktop_owner_unavailable') from exc
            # Recheck after discovery. The user may have answered on the desktop.
            current = self.route(session, question)
            if not current or any(current.get(k)!=route.get(k) for k in ('thread','call','index','question','options','kind')): raise DeliveryError('question_changed')
            route=current
            text = reply_envelope(route, answer)
            input_items = [{'type':'text','text':text,'text_elements':[]}]
            client_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'snowball-answer:'+request_id))
            if route['activity'] in ('running','thinking'):
                params = {'conversationId':route['thread'], 'input':input_items,
                    'clientUserMessageId':client_id, 'restoreMessage':{'id':client_id,'context':{'prompt':text}}}
                method, version = 'thread-follower-steer-turn', 1
            else:
                params = {'conversationId':route['thread'], 'turnStart':{'request':{
                    'threadId':route['thread'],'input':input_items,'clientUserMessageId':client_id},
                    'context':{'inheritThreadSettings':True}}}
                method, version = 'thread-follower-start-turn', 2
            try:
                result = ipc.request(method, params, version, owner)
            except DeliveryError:
                raise  # Never retry or switch to a new turn after ambiguous delivery.
            except (OSError, TimeoutError, ValueError) as exc:
                raise DeliveryError('delivery_unconfirmed', True) from exc
            value = result.get('result', {}).get('result')
            if not isinstance(value, dict) or not (value.get('turnId') or value.get('turn', {}).get('id')):
                raise DeliveryError('delivery_unconfirmed', True)
            # This is a turn/message receipt, not an acknowledgement that the
            # Desktop question card closed. Hook/Hub state is independent of it.
            return {'status':'accepted'}
        finally:
            ipc.close()
