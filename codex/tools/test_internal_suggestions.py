"""Replay Desktop's internal suggestion lifecycle against the actual Hook/Hub.
All events are isolated fixtures; no real AI request, user reply or BLE write.
"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'companion'))
from status_hub import StatusHub
spec=importlib.util.spec_from_file_location('suggestion_hook',ROOT/'plugins/xueqiu-status/scripts/codex_hook.py')
hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)

class InternalSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.hub=StatusHub(self.root/'hub');self.events=[]
        metadata=patch.object(hook,'session_title',return_value=None)
        self.metadata=metadata.start();self.addCleanup(metadata.stop)
    def send(self,sid,name,**extra):
        def emit(event):self.events.append(event);self.hub.accept(event)
        self.assertTrue(hook.process_hook(dict(session_id=sid,hook_event_name=name,**extra),self.root/'adapter',emit))
    def record(self,sid):
        return json.loads((self.root/'adapter/hooks'/f'{hook.opaque(sid)}.json').read_text())
    def prediction(self,sid='ephemeral'):
        self.send(sid,'SessionStart',source='fork',transcript_path=None)
        self.send(sid,'UserPromptSubmit',transcript_path=None,prompt=hook.DESKTOP_SUGGESTION_PREFIX+'\nPRIVATE_CONTENT')
    def test_finished_chat_is_not_preempted_by_internal_prediction(self):
        self.metadata.return_value='真实会话'
        self.send('real','UserPromptSubmit',transcript_path='/not-read.jsonl',prompt='PRIVATE_INPUT')
        self.send('real','Stop',transcript_path='/not-read.jsonl')
        original=self.hub.snapshot();self.metadata.return_value=None
        self.prediction()
        for event in ['PreToolUse','PostToolUse','Stop','SessionEnd']:
            self.send('ephemeral',event,transcript_path=None,tool_use_id='internal-tool')
            value=self.hub.snapshot()
            self.assertEqual(value['focused_session'],original['focused_session'])
            self.assertEqual(value['state'],'completed');self.assertEqual(value['display_name'],'真实会话')
        self.assertEqual(len(self.events),2)
        record=self.record('ephemeral');self.assertEqual(record['last_delivery']['reason'],'desktop_composer_suggestion')
        self.assertNotIn('PRIVATE_CONTENT',json.dumps(record));self.assertNotIn('prompt',record)
    def test_persistent_unnamed_chat_still_runs_and_even_quoted_marker_passes(self):
        self.metadata.return_value=''
        self.send('new','SessionStart',source='fork',transcript_path='/not-read.jsonl')
        self.send('new','UserPromptSubmit',transcript_path='/not-read.jsonl',prompt=hook.DESKTOP_SUGGESTION_PREFIX)
        self.assertEqual(self.hub.snapshot()['state'],'thinking');self.assertEqual(len(self.events),2)
        self.assertNotIn('internal_session',self.record('new'))
    def test_real_ephemeral_cli_and_side_chat_prompts_still_run(self):
        for source in ['startup','fork']:
            sid='human-'+source
            self.send(sid,'SessionStart',source=source,transcript_path=None)
            self.send(sid,'UserPromptSubmit',transcript_path=None,prompt='真正用户的问题')
            self.assertEqual(self.events[-1]['session_id'],hook.opaque(sid));self.assertEqual(self.events[-1]['phase'],'thinking')
            self.send(sid,'Stop',transcript_path=None);self.assertEqual(self.events[-1]['phase'],'completed')
    def test_missing_optional_metadata_is_not_filtered(self):
        self.send('legacy','UserPromptSubmit',prompt=hook.DESKTOP_SUGGESTION_PREFIX)
        self.assertEqual(self.events[-1]['phase'],'thinking')
    def test_omitted_start_and_empty_transcript_still_classify_prediction(self):
        self.send('ephemeral','UserPromptSubmit',transcript_path='',prompt=hook.DESKTOP_SUGGESTION_PREFIX)
        self.assertEqual(self.events,[])
    def test_classification_survives_hook_process_and_later_real_fork_promotes(self):
        self.prediction();self.send('ephemeral','Stop')
        self.assertEqual(self.events,[])
        self.send('ephemeral','UserPromptSubmit',transcript_path='/now-persistent.jsonl',prompt='真实工作')
        self.assertEqual(self.events[-1]['phase'],'thinking');self.assertNotIn('internal_session',self.record('ephemeral'))
    def test_unrelated_transient_prompt_is_not_classified_by_substring(self):
        self.send('human','UserPromptSubmit',transcript_path=None,prompt='请解释：'+hook.DESKTOP_SUGGESTION_PREFIX)
        self.assertEqual(self.events[-1]['phase'],'thinking')
    def test_real_question_after_completion_keeps_priority_and_waiting(self):
        self.send('real','Stop',transcript_path='/not-read.jsonl')
        self.prediction()
        self.send('other','PreToolUse',transcript_path='/another.jsonl',tool_use_id='question',
                  tool_name='request_user_input_async',tool_input={'questions':[{'title':'请选择'}]})
        self.assertEqual(self.hub.snapshot()['session_id'],hook.opaque('other'));self.assertEqual(self.hub.snapshot()['state'],'waiting')

if __name__=='__main__':unittest.main()
