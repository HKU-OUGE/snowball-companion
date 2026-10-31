"""Read-only local Codex Desktop runtime inventory, separate from sidebar labels.

notLoaded is a server runtime state, not an inferred result from the last turn.
The directory is enumerated independently. Rows need a fresh runtime status or
an unchanged catalog revision with a previously observed unloaded runtime.
Never resumes a conversation, reads a prompt for inference, or sends a message.
"""
import time
import uuid

KNOWN = {'idle', 'notLoaded', 'active', 'systemError'}
TTL = 20
COLD_READ_LIMIT = 2

class CodexActivity:
    def __init__(self, tools, clock=time.time):
        self.tools, self.clock = tools, clock
        self.last_poll = 0
        self.cached = {}
        self.value = {'scope': 'codex-desktop-local', 'complete': False}

    def refresh(self, directory, records=None):
        now=self.clock()
        # Live hooks are sufficient to veto an installation. Do not ask the
        # desktop to load histories while the user is actively working.
        if records is not None and any(r.get('provider')=='Codex' and
            r.get('phase') in ('thinking','running','waiting','failed') and
            min(r.get('expires_at',0),r.get('task_expires_at') or 0)>now
            for r in records.values()):
            self.value={'scope':'codex-desktop-local','complete':False,
                'observed_at':now,'reason':'live_codex_activity'}
            return
        if self.clock()-self.last_poll < 15:
            return
        self.last_poll = self.clock()
        failed = {'scope': 'codex-desktop-local', 'complete': False,
                  'observed_at': self.clock(), 'reason': 'runtime_unavailable'}
        try:
            if not directory.fresh() or not self.tools.available():
                self.value = failed
                return
            rows = {key:dict(row) for key,row in directory.rows.items()}
            if not rows:
                self.value = dict(failed, reason='no_caller')
                return
            caller = next(iter(rows.values()))['thread']
            request = uuid.uuid4().hex
            listing = self.tools.call('list_threads', {'limit': 50}, caller, request)
            source_errors=listing.get('unavailableSources') or []
            host_errors=listing.get('unavailableHosts') or []
            unrelated_source=lambda e: e=='chatgpt' or (isinstance(e,dict) and e.get('source',e.get('kind'))=='chatgpt')
            unrelated_host=lambda e: isinstance(e,dict) and isinstance(e.get('hostId'),str) and e['hostId']!='local'
            if (listing.get('schemaVersion') not in (1,4) or
                any(not unrelated_source(e) for e in source_errors) or any(not unrelated_host(e) for e in host_errors)):
                raise ValueError('incomplete_runtime')
            entries = listing.get('threads')
            pinned = listing.get('pinnedThreads')
            if not isinstance(entries, list) or not isinstance(pinned, list):
                raise ValueError('invalid_runtime')
            states = {}
            for row in entries+pinned:
                if row.get('kind') == 'codex' and row.get('hostId') == 'local':
                    states[row['id']] = row.get('status')
            if any(s not in KNOWN for s in states.values()):
                raise ValueError('unknown_runtime_state')
            # A current runtime veto is enough. read_thread may load a large
            # history even when its output is truncated, so do not read cold
            # histories while any visible runtime is already busy.
            if any(s in ('active','systemError') for s in states.values()):
                self.value = dict(failed, reason='runtime_codex_activity', any_active=True)
                return
            # The sidebar is capped. Warm absent entries gradually rather than
            # triggering an unbounded history-loading burst after a restart.
            missing=[]
            for row in rows.values():
                thread=row['thread'];cached=self.cached.get(thread)
                if thread in states:continue
                # Reuse only unloaded threads whose catalog revision is
                # unchanged. Resumed chats move into the fresh runtime list;
                # a changed revision always requires a new metadata read.
                if cached and cached[0]==row.get('updated_at') and cached[1]=='notLoaded':
                    states[thread]=cached[1]
                else:missing.append(thread)
            def read(thread):
                result = self.tools.call('read_thread', {'threadId':thread, 'hostId':'local',
                    'turnLimit':1, 'includeOutputs':False, 'maxOutputCharsPerItem':1}, caller, uuid.uuid4().hex)
                value = result.get('thread', {})
                if value.get('id') != thread or value.get('hostId') != 'local':
                    raise ValueError('wrong_runtime_identity')
                return thread, value.get('status', {}).get('type')
            for thread in missing[:COLD_READ_LIMIT]:
                # Do not start another expensive read late in the observation
                # window. A slow in-flight call still invalidates this pass.
                if self.clock()-self.last_poll >= TTL/2:
                    break
                identity,state=read(thread)
                states[identity]=state
                if state in ('active','systemError'):
                    break
            if any(s not in KNOWN for s in states.values()):
                raise ValueError('unknown_runtime_state')
            directory.refresh(force=True)
            if (not directory.fresh() or set(rows) != set(directory.rows) or
                any(row.get('updated_at') != directory.rows[key].get('updated_at')
                    for key,row in rows.items())):
                raise ValueError('inventory_changed')
            observed = self.last_poll
            if self.clock()-observed >= TTL:
                raise ValueError('slow_runtime_inventory')
            # Partial cache progress is reusable only after the same complete
            # directory/revision validation as a full observation. Active/idle
            # histories never become indefinitely reusable unloaded evidence.
            self.cached={row['thread']:(row.get('updated_at'),'notLoaded')
                         for row in rows.values() if states.get(row['thread'])=='notLoaded'}
            remaining=sum(row['thread'] not in states for row in rows.values())
            if remaining:
                self.value=dict(failed,reason='inventory_warming',remaining=remaining,
                                count=len(rows),any_active=any(s in ('active','systemError') for s in states.values()))
                return
            self.value = {'scope':'codex-desktop-local', 'complete':True,
                'observed_at':observed, 'expires_at':observed+TTL,
                'sessions':{key:states[row['thread']] for key,row in rows.items()},
                'any_active':any(s in ('active','systemError') for s in states.values()),
                'count':len(rows)}
        except Exception as exc:
            # Codes only; never log tool output, conversations or credentials.
            code=getattr(exc,'code',None)
            if isinstance(code,str) and code.replace('_','').isalnum():
                failed['reason']=code
            elif isinstance(exc,ValueError) and str(exc) in ('incomplete_runtime','invalid_runtime','wrong_runtime_identity','unknown_runtime_state','inventory_changed','slow_runtime_inventory'):
                failed['reason']=str(exc)
            self.value = failed
