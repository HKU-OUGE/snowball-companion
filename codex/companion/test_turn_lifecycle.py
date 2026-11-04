"""Metadata-only turn events fill gaps in app/voice-started hook delivery."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

from codex_desktop import CodexDesktop, VisibleSummary, opaque
from status_hub import StatusHub, Invalid
from ota_idle import idle_evidence
from test_sidebar_tasks import SidebarDirectory


def append(path, kind, at, turn='turn-a', **changes):
    payload = dict(type=kind, turn_id=turn, root_turn_id=turn)
    payload.update(changes)
    event = dict(type='event_msg', timestamp=datetime.fromtimestamp(at, timezone.utc).isoformat(), payload=payload)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(event, ensure_ascii=False)+'\n')


class MetadataReader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'events.jsonl'
        self.now = time.time()-2
        self.reader = VisibleSummary()

    def test_explicit_start_and_complete_without_any_hook(self):
        append(self.path, 'task_started', self.now)
        self.assertEqual(self.reader.read(self.path), {})
        self.assertEqual(self.reader.lifecycle['phase'], 'thinking')
        append(self.path, 'task_complete', self.now+1, last_agent_message='PRIVATE FINAL')
        self.reader.read(self.path)
        self.assertEqual(self.reader.lifecycle['phase'], 'completed')
        self.assertNotIn('PRIVATE', json.dumps(self.reader.lifecycle))

    def test_child_turn_old_completion_and_future_events_do_not_replace_current(self):
        append(self.path, 'task_started', self.now)
        append(self.path, 'task_started', self.now+.1, turn='turn-b')
        append(self.path, 'task_complete', self.now+.2, turn='turn-a')
        append(self.path, 'task_complete', self.now+.3, turn='child', root_turn_id='turn-b')
        append(self.path, 'task_complete', self.now+100, turn='turn-b')
        self.reader.read(self.path)
        self.assertEqual(self.reader.lifecycle['phase'], 'thinking')
        self.assertEqual(self.reader.lifecycle['turn'], opaque('turn-b'))
        self.assertAlmostEqual(self.reader.lifecycle['at'], self.now+.1, places=5)

    def test_text_and_reasoning_are_not_activity_or_completion(self):
        for kind in ('agent_reasoning', 'agent_message', 'function_call_output'):
            append(self.path, kind, self.now, text='task_started task_complete PRIVATE')
        self.reader.read(self.path)
        self.assertEqual(self.reader.lifecycle, {})

    def test_abort_and_incremental_partial_line(self):
        append(self.path, 'task_started', self.now)
        self.reader.read(self.path)
        event = json.dumps({'type':'event_msg','timestamp':datetime.fromtimestamp(self.now+1, timezone.utc).isoformat(),
                           'payload':{'type':'turn_aborted','turn_id':'turn-a','root_turn_id':'turn-a'}})
        with self.path.open('a') as stream:stream.write(event[:45])
        self.reader.read(self.path);self.assertEqual(self.reader.lifecycle['phase'],'thinking')
        with self.path.open('a') as stream:stream.write(event[45:]+'\n')
        self.reader.read(self.path);self.assertEqual(self.reader.lifecycle['phase'],'idle')


class LifecycleProjection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now = time.time()
        self.key = 'codex-hooks/'+opaque('thread-fixture')
        self.desktop = SimpleNamespace(summaries={},lifecycles={},
            directory=SimpleNamespace(rows={self.key:{'display_name':'Fixture'}},fresh=lambda:True),
            available=lambda:True,app_tools=SimpleNamespace(available=lambda:True),route=lambda *_:None)
        self.hub = StatusHub(Path(self.tmp.name),lambda:self.now,desktop=self.desktop)
        self.event('completed',self.now-100,1)

    def event(self, phase, at, seq, **changes):
        event=dict(version=1,provider='Codex',source='codex-hooks',session_id=opaque('thread-fixture'),
                   seq=seq,event_id=str(seq),kind='task',phase=phase,timestamp=at,ttl_seconds=300)
        event.update(changes);self.hub.accept(event)

    def lifecycle(self, phase='thinking', at=None):
        self.desktop.lifecycles[self.key]={'phase':phase,'at':self.now if at is None else at,
                                          'observed_at':self.now,'turn':opaque('turn-fixture')}

    def test_new_start_overrides_old_done_without_rewriting_hook_sequence_or_leases(self):
        before=json.loads(json.dumps(self.hub.records[self.key]))
        self.lifecycle();snapshot=self.hub.snapshot()
        self.assertEqual(snapshot['state'],'thinking')
        self.assertEqual(snapshot['focused_session'],self.key)
        self.assertEqual(snapshot['state_evidence'],'codex_turn_lifecycle')
        self.assertTrue(snapshot['source_fresh'])
        self.assertEqual(self.hub.records[self.key],before)
        self.now+=1;self.event('running',self.now,2)
        self.assertEqual(self.hub.snapshot()['state'],'running')
        self.now+=1;self.lifecycle('completed')
        self.assertEqual(self.hub.snapshot()['state'],'completed')

    def test_unresolved_question_survives_start_and_completion(self):
        self.event('waiting',self.now-.5,2,pending_input=1,questions=[{'id':'a'*32,'text':'需要确认'}])
        for phase in ('thinking','completed'):
            self.lifecycle(phase)
            snapshot=self.hub.snapshot()
            self.assertEqual(snapshot['state'],'waiting');self.assertEqual(snapshot['pending_input'],1)

    def test_rereading_does_not_extend_task_lease_or_make_expired_active_idle(self):
        self.lifecycle(at=self.now-301)
        # The lifecycle must be newer than its preceding completion.
        self.hub.records[self.key]['phase_at']=self.now-400
        snapshot=self.hub.snapshot()
        self.assertEqual(snapshot['last_state'],'thinking')
        self.assertEqual(snapshot['status'],'unknown')
        self.assertFalse(snapshot['task_fresh'])

    def test_directory_presence_or_delivery_acceptance_does_not_create_activity(self):
        self.hub.messages['fixture']={'status':'accepted','at':self.now}
        self.assertEqual(self.hub.snapshot()['state'],'completed')
        self.lifecycle();self.desktop.directory.rows={}
        self.assertNotEqual(self.hub.snapshot().get('state'),'thinking')

    def test_new_lifecycle_revokes_older_idle_installation_evidence(self):
        self.desktop.activity=SimpleNamespace(value={'scope':'codex-desktop-local','complete':True,
            'observed_at':self.now-1,'expires_at':self.now+19,'sessions':{self.key:'idle'},'any_active':False})
        self.assertTrue(idle_evidence(self.hub.snapshot(), self.now))
        self.lifecycle()
        self.assertFalse(idle_evidence(self.hub.snapshot(), self.now))

    def test_metadata_active_session_cannot_be_evicted_as_old_completed_history(self):
        from unittest.mock import patch
        raw=self.hub.records[self.key]
        raw['expires_at']=raw['task_expires_at']=self.now-1
        self.lifecycle()
        with patch('status_hub.MAX_SESSIONS',1), self.assertRaises(Invalid):
            self.hub.accept(dict(version=1,provider='Codex',source='test',session_id='another',
                seq=1,event_id='new',timestamp=self.now,kind='task',phase='running'))
        self.assertIn(self.key,self.hub.records)


class DesktopIntegration(SidebarDirectory):
    def test_real_indexed_metadata_updates_hub_without_hooks(self):
        path=self.root/'sessions'/'1.jsonl';path.parent.mkdir()
        self.add(1,rollout_path=str(path))
        desktop=CodexDesktop(self.home);desktop.available=lambda:True
        desktop.app_tools=Mock();desktop.app_tools.available.return_value=True
        desktop.directory.refresh()
        hub=StatusHub(self.home/'hub',desktop=desktop);hub.sync_directory()
        self.assertEqual(hub.snapshot()['state'],'idle')
        append(path,'task_started',time.time())
        desktop.refresh(hub.records)
        snapshot=hub.publish()
        self.assertEqual(snapshot['state'],'thinking')
        self.assertEqual(snapshot['seq'],0)
        self.assertTrue(hub.records[snapshot['focused_session']]['catalog_only'])
        append(path,'task_complete',time.time())
        desktop.refresh(hub.records)
        self.assertEqual(hub.snapshot()['state'],'completed')
        desktop.app_tools.call.assert_not_called()


if __name__=='__main__':unittest.main()
