import importlib.util,sys
from pathlib import Path
from bridge import make_packet,wire_state
P=Path(__file__).resolve().parents[1]/'plugins/xueqiu-status/scripts/codex_hook.py'
spec=importlib.util.spec_from_file_location('compaction_hook',P);hook=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook)
def test_only_real_compaction_hooks_change_phase():
 r={};assert hook.hook_phase({'hook_event_name':'PreCompact','trigger':'auto'},r)=='compacting'
 assert r['activity_phase']=='compacting'
 assert hook.hook_phase({'hook_event_name':'PostCompact'},r)=='thinking'
 assert hook.hook_phase({'hook_event_name':'PreToolUse','tool_name':'Bash','tool_use_id':'x','tool_input':{'command':'zip -r archive .'}},r)=='running'
def test_question_preempts_compaction_until_actual_answer():
 r={'pending_questions':{'opaque-call':[0]},'question_details':{}}
 assert hook.hook_phase({'hook_event_name':'PreCompact'},r)=='waiting'
 assert r['activity_phase']=='compacting'
 assert hook.hook_phase({'hook_event_name':'PostCompact'},r)=='waiting'
 assert hook.hook_phase({'hook_event_name':'Interrupt'},r)=='waiting'
def test_old_firmware_receives_thinking_new_firmware_receives_compacting():
 info={'accepted':0,'nonce':'00'*8}
 assert make_packet(b'k'*32,info,'Codex','compacting')[2]==1
 assert make_packet(b'k'*32,{**info,'compaction_v':1},'Codex','compacting')[2]==6
