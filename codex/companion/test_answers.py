import json
import tempfile
import time
from pathlib import Path
import unittest
from unittest.mock import Mock

from status_hub import StatusHub, Invalid
from codex_desktop import CodexDesktop, DesktopIPC, DeliveryError, VisibleSummary, reply_envelope, opaque
from attention_relay import question_packets
from test_attention_native import NativeAttentionTests

Q='1'*32
class Answers(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.desktop=Mock();self.desktop.available.return_value=True;self.desktop.summaries={}
  self.desktop.route.return_value={'question':'接下来怎样做？','options':['先看方案','继续执行']};self.desktop.deliver.return_value={'status':'accepted'}
  self.hub=StatusHub(Path(self.temp.name),desktop=self.desktop)
  self.hub.accept(dict(version=1,provider='Codex',source='test',session_id='a',seq=1,event_id='a',timestamp=time.time(),kind='task',phase='waiting',pending_input=1,questions=[{'id':Q,'text':'接下来怎样做？','options':['先看方案','继续执行']}]))
  self.req={'session':'test/a','question':Q,'request_id':'2'*32,'choice':0}
 def test_explicit_choice_delivered_once_and_no_answer_saved(self):
  r=self.hub.answer(self.req);self.assertEqual(r['delivery'],'accepted')
  self.hub.answer({**self.req,'request_id':'3'*32})
  with self.assertRaises(Invalid):self.hub.answer({**self.req,'request_id':'4'*32,'choice':1})
  self.desktop.deliver.assert_called_once_with('test/a',Q,'先看方案','2'*32)
  self.assertNotIn('先看方案',self.hub.answer_path.read_text())
 def test_invalid_or_stale_or_unavailable_never_delivered(self):
  for change in [{'choice':-1},{'choice':True},{'choice':2},{'question':'a'*32},{'text':'yes'},{'approve':True}]:
   with self.assertRaises(Invalid):self.hub.answer({**self.req,**change})
  self.desktop.available.return_value=False
  with self.assertRaises(Invalid):self.hub.answer(self.req)
  self.desktop.deliver.assert_not_called()
 def test_failed_confirmation_is_not_success_or_automatic_retry(self):
  self.desktop.deliver.side_effect=DeliveryError('delivery_unconfirmed',True)
  self.assertEqual(self.hub.answer(self.req)['delivery'],'unconfirmed')
  self.assertEqual(self.hub.answer(self.req)['delivery'],'unconfirmed')
  self.desktop.deliver.assert_called_once()
  restarted=StatusHub(Path(self.temp.name),desktop=self.desktop)
  self.assertEqual(restarted.answer(self.req)['delivery'],'unconfirmed')
 def test_freeform_and_changed_choices(self):
  req={k:v for k,v in self.req.items() if k!='choice'}
  self.desktop.route.return_value={'options':['changed']}
  with self.assertRaises(Invalid):self.hub.answer({**req,'text':'自定义回答'})
  self.desktop.route.return_value={'question':'接下来怎样做？','options':['先看方案','继续执行']}
  self.assertEqual(self.hub.answer({**req,'text':'自定义回答'})['delivery'],'accepted')
 def test_lock_not_held_during_ipc(self):
  import threading
  def deliver(*_):
   lock_taken=[]
   def run():
    with self.hub.lock:lock_taken.append(True)
   t=threading.Thread(target=run);t.start();t.join(1);self.assertEqual(lock_taken,[True])
  self.desktop.deliver.side_effect=deliver;self.hub.answer(self.req)

class Summaries(unittest.TestCase):
 def test_only_visible_progress_and_reset_on_new_turn(self):
  with tempfile.TemporaryDirectory() as root:
   p=Path(root)/'rollout.jsonl';stamp='2026-10-18T00:00:00Z'
   def write(payload,typ='response_item'):
    with p.open('a') as f:f.write(json.dumps({'type':typ,'timestamp':stamp,'payload':payload})+'\n')
   write({'turn_id':'t1'},'turn_context')
   write({'type':'message','role':'assistant','phase':'commentary','content':[{'type':'output_text','text':'正在检查串口连接'}]})
   reader=VisibleSummary();self.assertEqual(reader.read(p)['text'],'正在检查串口连接')
   for payload in [{'type':'reasoning','text':'private reasoning'},{'type':'custom_tool_call_output','output':'SECRET'}, {'type':'message','role':'assistant','phase':'commentary','channel':'analysis','content':[{'type':'output_text','text':'SECRET'}]}]:write(payload)
   self.assertEqual(reader.read(p)['text'],'正在检查串口连接')
   write({'turn_id':'t2'},'turn_context');self.assertEqual(reader.read(p),{})
 def test_envelope_preserves_exact_question_and_user_answer(self):
  route={'call':'call_xyz','index':2,'question':'你选哪个？'}
  envelope=reply_envelope(route,'第二个')
  body=json.loads(envelope.split('\n')[1]);self.assertEqual(json.loads(body[0]['questionItemId']),['request_user_input_async','call_xyz',2]);self.assertEqual(body[0]['answer'],'第二个')

class OptionsWire(NativeAttentionTests):
 def test_options_require_atomic_complete_utf8(self):
  row={**self.row,'options':['开始测试','中文'*40], 'answerable':True}
  packets=list(question_packets(1,[row],1,choices=True));self.assertTrue(all(len(b)+47<=272 for _,b in packets))
  for op,b in packets[:-1]:self.assertEqual(self.push(op,b),0)
  self.assertEqual(self.lib.count(),0);self.assertEqual(self.push(*packets[-1]),1)
  self.lib.reset()
  for op,b in packets:
   if op==5:continue
   if op==2:self.assertEqual(self.push(op,b),-1)
   else:self.push(op,b)


class DesktopContract(unittest.TestCase):
 def test_steer_supplies_restoration_context_and_targets_exact_owner(self):
  calls=[]
  class IPC:
   def __init__(self,*_):pass
   def request(self,method,params,version,target=None):
    calls.append((method,params,version,target))
    if method=='thread-owner-discovery':return {'handledByClientId':'owner'}
    self_test.assertEqual(params['restoreMessage']['context']['prompt'],params['input'][0]['text'])
    self_test.assertEqual(params['conversationId'],'thread-a')
    self_test.assertEqual(target,'owner')
    return {'result':{'result':{'turnId':'turn-a'}}}
   def close(self):pass
  self_test=self
  desktop=CodexDesktop(ipc_factory=IPC)
  route={'thread':'thread-a','call':'call-a','index':0,'question':'选择？','options':['A','B'],'kind':'async','activity':'running'}
  desktop.route=Mock(return_value=route)
  self.assertEqual(desktop.deliver('test/a',Q,'A','2'*32),{'status':'accepted'})
  self.assertEqual([c[0] for c in calls],['thread-owner-discovery','thread-follower-steer-turn'])
 def test_read_only_discovery_failure_is_known_unsent(self):
  class IPC:
   def __init__(self,*_):pass
   def request(self,*_):raise DeliveryError('timeout',True)
   def close(self):pass
  desktop=CodexDesktop(ipc_factory=IPC);desktop.route=Mock(return_value={'thread':'a'})
  with self.assertRaises(DeliveryError) as result:desktop.deliver('test/a',Q,'A','2'*32)
  self.assertFalse(result.exception.uncertain)
