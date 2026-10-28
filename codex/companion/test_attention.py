import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from attention_model import clean_question, projection
from attention_relay import AttentionRelay, question_packets, rows_for
from status_hub import StatusHub, Invalid
import bridge

Q1='1'*32;Q2='2'*32
class AttentionTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.now=1000.;self.hub=StatusHub(Path(self.temp.name),lambda:self.now);self.seq=0
 def tearDown(self): self.temp.cleanup()
 def send(self,qs=None,session='a',activity='running'):
  self.seq+=1;qs=qs if qs is not None else [{'id':Q1,'text':'需要先查看手机排版吗？'}]
  self.hub.accept(dict(version=1,provider='Codex',source='test',session_id=session,event_id=str(self.seq),seq=self.seq,timestamp=self.now,kind='task',phase='waiting' if qs else activity,activity_phase=activity,pending_input=len(qs),questions=qs,ttl_seconds=300))
 def action(self,action,q=Q1):return self.hub.set_attention({'session':'test/a','question':q,'action':action})
 def test_snooze_expires_without_new_activity(self):
  self.send();stamp=self.hub.snapshot()['task_updated_at'];self.action('snooze')
  self.now+=301;snap=self.hub.snapshot();self.assertEqual(snap['state'],'idle');self.assertEqual(snap['status'],'quiet_attention');self.assertFalse(snap['task_fresh']);self.assertEqual(bridge.parse_source(snap,self.now).state,'idle')
  self.now+=300;snap=self.hub.snapshot();self.assertEqual(snap['state'],'waiting');self.assertFalse(snap['task_fresh']);self.assertEqual(snap['task_updated_at'],stamp)
 def test_ignoring_does_not_answer_and_new_questions_preempt(self):
  self.send();self.action('ignore');self.send();snap=self.hub.snapshot();self.assertEqual(snap['pending_input'],0);self.assertEqual(snap['pending_total'],1);self.assertEqual(snap['state'],'running')
  self.send([{'id':Q1,'text':'需要先查看手机排版吗？'},{'id':Q2,'text':'新问题'}]);self.assertEqual(self.hub.snapshot()['pending_input'],1)
  self.action('ignore',Q2);self.assertEqual(self.hub.snapshot()['pending_input'],0)
  self.action('restore');self.assertEqual(self.hub.snapshot()['pending_input'],1)
 def test_priority_resumes_other_session(self):
  self.send([],session='b');self.send();self.assertEqual(self.hub.snapshot()['session_id'],'a');self.action('ignore');self.assertEqual(self.hub.snapshot()['session_id'],'b')
 def test_repeat_snooze_does_not_extend_or_heartbeats_change_it(self):
  self.send();first=self.action('snooze')['questions'][0]['until'];self.now+=80;self.action('snooze');self.send();self.assertEqual(self.hub.snapshot()['questions'][0]['until'],first)
 def test_restore_and_restart_and_answer_cleanup(self):
  self.send();self.action('ignore');self.hub=StatusHub(Path(self.temp.name),lambda:self.now);self.assertEqual(self.hub.snapshot()['questions'][0]['status'],'ignored');self.send([]);self.assertEqual(self.hub.snapshot()['questions'],[])
  with self.assertRaises(Invalid):self.action('restore')
 def test_wrong_question_cannot_change_another(self):
  self.send()
  with self.assertRaises(Invalid):self.action('ignore',Q2)
  self.assertEqual(self.hub.snapshot()['pending_input'],1)
 def test_bound_and_control_characters(self):
  text=clean_question('问'*500);self.assertTrue(text.endswith('…'));self.assertLessEqual(len(text.encode()),768);self.assertLessEqual(len(text),256)
  self.assertEqual(clean_question('甲\n乙\u202e'),'甲 乙')
 def test_packet_limits_and_quiet_rows_retained(self):
  self.send([{'id':Q1,'text':clean_question('问'*500)}]);self.action('ignore');rows,total=rows_for(self.hub.snapshot(),'Codex',self.now)
  self.assertEqual((len(rows),total),(1,1));self.assertEqual(rows[0]['status'],'ignored')
  packets=list(question_packets(1,rows,total));self.assertTrue(all(len(body)+47<=272 for _,body in packets));self.assertEqual(sum(len(body)-42 for op,body in packets if op==1),len(rows[0]['text'].encode()))
 def test_stale_hub_cannot_renew_questions(self):
  self.send();self.assertEqual(rows_for(self.hub.snapshot(),'Codex',self.now+31),([],0))
 def test_action_can_only_control_reminders(self):
  self.send()
  with self.assertRaises(Invalid):self.action('approve')
  with self.assertRaises(Invalid):self.action('cancel')

class RelayTest(unittest.IsolatedAsyncioTestCase):
 async def test_signed_packets_and_authenticated_reverse_action(self):
  with tempfile.TemporaryDirectory() as root:
   hub=StatusHub(Path(root));now=__import__('time').time()
   hub.accept(dict(version=1,provider='Codex',source='test',session_id='a',event_id='a',seq=1,timestamp=now,kind='task',phase='waiting',activity_phase='running',pending_input=1,questions=[{'id':Q1,'text':'请确认测试问题'}],ttl_seconds=300))
   cfg={'key':'ab'*32};info={'accepted':10,'nonce':'12'*8,'attention_v':1,'catalog_v':2,'request':None}
   client=type('Client',(),{'write_gatt_char':AsyncMock()})();relay=AttentionRelay()
   with patch.object(bridge,'read_info',AsyncMock(return_value=info)):
    await relay.step(client,cfg,hub.snapshot())
   for call in client.write_gatt_char.call_args_list:
    packet=call.args[1];self.assertEqual(packet[0],6);self.assertEqual(packet[-32:],hmac.digest(bytes.fromhex(cfg['key']),packet[:-32],'sha256'));self.assertLessEqual(len(packet),272)
   sid=bridge.wire_id({'source':'test','session_id':'a'})
   request=dict(seq=7,provider=0,generation=1,pending=True,id=sid,action=3,before=Q1)
   raw=struct.pack('<8sIBIB16sB16s',bytes.fromhex(info['nonce']),7,0,1,True,bytes.fromhex(sid),3,bytes.fromhex(Q1));request['proof']=hmac.digest(bytes.fromhex(cfg['key']),raw,'sha256').hex();info['request']=request;relay.last_poll=float('-inf')
   with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch('attention_relay.attention_http',side_effect=hub.set_attention):
    result=await relay.step(client,cfg,hub.snapshot())
   self.assertEqual(result['questions'][0]['status'],'snoozed');self.assertEqual(relay.results[0],(7,1));self.assertEqual(hub.records['test/a']['pending_input'],1)
 async def test_production_transport_preserves_retryable_error_class(self):
  read=AsyncMock(side_effect=bridge.InfoTransportError('truncated'))
  relay=AttentionRelay(read,bridge.verify_request)
  with self.assertRaises(bridge.InfoTransportError):await relay.step(object(),{},None)
  read.assert_awaited_once()
 async def test_old_firmware_skipped(self):
  relay=AttentionRelay();client=type('Client',(),{'write_gatt_char':AsyncMock()})()
  with patch.object(bridge,'read_info',AsyncMock(return_value={'attention_v':0})):
   await relay.step(client,{},None)
  client.write_gatt_char.assert_not_called()

class IdleEvidenceTests(unittest.TestCase):
 def test_empty_live_hub_is_waiting_for_work_not_stale(self):
  with tempfile.TemporaryDirectory() as root:
   hub=StatusHub(Path(root),lambda:1000.);s=hub.snapshot();self.assertEqual(s['status'],'ready');t=bridge.parse_source(s,1000);self.assertEqual(t.state,'idle');self.assertIsNone(t.task_updated_at);self.assertIsNone(t.session_id)
   with self.assertRaises(bridge.SourceUnavailable):bridge.parse_source(s,1031)
 def test_completed_expiry_becomes_ready_but_stale_work_is_unknown(self):
  for phase in ['completed','idle','running','thinking','waiting','failed']:
   with self.subTest(phase=phase), tempfile.TemporaryDirectory() as root:
    clock=[1000.];hub=StatusHub(Path(root),lambda:clock[0]);hub.accept(dict(version=1,provider='Codex',source='test',session_id='a',event_id='a',seq=1,timestamp=1000,kind='task',phase=phase,ttl_seconds=30));clock[0]+=31;s=hub.snapshot()
    self.assertEqual(s['status'],'ready' if phase in ('completed','idle') else 'unknown')
    if phase in ('completed','idle'):self.assertEqual(bridge.parse_source(s,clock[0]).state,'idle');self.assertFalse(s['task_fresh'])
    else:
     with self.assertRaises(bridge.SourceUnavailable):bridge.parse_source(s,clock[0])
