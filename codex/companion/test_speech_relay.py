"""Exercise the upload -> review -> explicit-send boundary without any real IPC."""
import asyncio
import hashlib
import hmac
import json
import struct
import time
import unittest
import zlib
from unittest.mock import patch

from speech_relay import SpeechRelay, SpeechServiceError


class Device:
    def __init__(self):
        self.key=b'k'*32
        self.nonce=b'n'*8
        self.accepted=0
        self.audio=bytes(range(256))*32
        self.target='codex-hooks/'+'a'*32
        self.meta={'v':1,'id':42,'s':3,'p':0,
            'sid':hashlib.sha256(self.target.encode()).hexdigest()[:32],
            'qid':'','n':len(self.audio),'samples':len(self.audio)*2,
            'crc':zlib.crc32(self.audio),'busy':0}
        self.transcript=bytearray()
        self.statuses=[]
        self.relay=None
        self.read_notifications=False
        self.processing=False
        self.pull_counts=[]

    async def info(self,client,config):
        return {'nonce':self.nonce.hex(),'accepted':self.accepted}

    async def read_gatt_char(self,uuid):
        raw=json.dumps(self.meta,separators=(',',':')).encode()
        envelope=json.dumps({'data':self.meta,
            'h':hmac.digest(self.key,b'XQVOICE'+self.nonce+raw,'sha256').hex()}).encode()
        if self.read_notifications:
            # BlueZ emits Value PropertiesChanged for ReadValue as well.
            self.relay.notify(None,envelope)
        return envelope

    async def write_gatt_char(self,uuid,packet,response):
        head,proof=packet[:-32],packet[-32:]
        assert hmac.compare_digest(proof,hmac.digest(self.key,head,'sha256'))
        version,provider,op,seq,nonce=struct.unpack('<BBBI8s',head[:15])
        assert (version,provider,nonce)==(8,0,self.nonce)
        assert seq==self.accepted+1
        self.accepted=seq
        payload=head[15:]
        if op==1:
            identity,offset,count=struct.unpack('<IIB',payload)
            self.pull_counts.append(count)
            # Realistic MTU-limited packets, shorter than the nominal 160.
            for at in range(offset,min(len(self.audio),offset+count*96),96):
                body=b'XQVC'+struct.pack('<II',identity,at)+self.audio[at:at+96]
                self.relay.notify(None,body+hmac.digest(self.key,self.nonce+body,'sha256')[:16])
            self.meta['s']=4
        elif op==2:
            self.transcript.clear()
        elif op==3:
            identity,offset=struct.unpack('<IH',payload[:6])
            assert offset==len(self.transcript)
            self.transcript.extend(payload[6:])
        elif op==4:
            identity,status=struct.unpack('<IB',payload)
            assert identity==self.meta['id']
            self.statuses.append(status)
            self.meta['s']={1:5,2:7}.get(status,8)
        elif op==5:
            assert struct.unpack('<I',payload)[0]==self.meta['id']
            assert self.meta['s']==4
            self.processing=True


class RelayFlow(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.device=Device()
        self.relay=SpeechRelay(self.device.info)
        self.device.relay=self.relay
        self.relay.attached=True
        self.relay.nonce=self.device.nonce.hex()
        self.relay.ready=True
        self.calls=[]
        self.patch=patch('speech_relay.local_http',side_effect=self.http)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def http(self,path,payload=None,port=4191,timeout=65):
        self.calls.append((path,payload))
        if path=='/v1/transcribe':return {'text':'请检查页面布局。'}
        if path in ('/v1/message','/v1/answer'):return {'delivery':'accepted'}
        raise AssertionError(path)

    async def pump(self,until):
        for _ in range(150):
            self.relay.last_poll=self.relay.last_meta=0
            self.relay.last_health=self.relay.last_lease=time.monotonic()
            snapshot={'hub_alive':True,'generated_at':time.time(),
                'sessions':[{'provider':'Codex','source':'codex-hooks','session_id':'a'*32,'questions':[]}]}
            await self.relay.step(self.device,{'key':self.device.key.hex()},snapshot)
            if until():return
            await asyncio.sleep(.01)
        self.fail('Speech pipeline did not reach expected phase')

    async def test_review_never_sends_until_explicit_device_confirm(self):
        await self.pump(lambda:self.device.meta['s']==5)
        self.assertEqual(self.device.transcript.decode(),'请检查页面布局。')
        self.assertEqual([c[0] for c in self.calls],['/v1/transcribe'])
        # A normal extra heartbeat must not send the reviewed transcript.
        await self.pump(lambda:True)
        self.assertEqual(len(self.calls),1)
        self.device.meta['s']=6
        await self.pump(lambda:self.device.meta['s']==7)
        self.assertEqual(self.calls[-1][0],'/v1/message')
        self.assertEqual(self.calls[-1][1]['session'],self.device.target)
        self.assertEqual(self.device.statuses,[1,2])

    async def test_bad_crc_never_reaches_recognizer(self):
        self.device.meta['crc']^=1
        await self.pump(lambda:self.device.meta['s']==8)
        self.assertEqual(self.device.statuses,[8])
        self.assertEqual(self.calls,[])

    async def test_bridge_restart_can_resume_an_unconfirmed_upload(self):
        self.device.meta['s']=4
        await self.pump(lambda:self.device.meta['s']==5)
        self.assertEqual(self.device.transcript.decode(),'请检查页面布局。')
        self.assertEqual([c[0] for c in self.calls],['/v1/transcribe'])

    async def test_bluez_read_notifications_do_not_abort_audio_upload(self):
        self.device.read_notifications=True
        await self.pump(lambda:self.device.meta['s'] in (5,8))
        self.assertFalse(self.relay.receive_error)
        self.assertEqual(self.device.meta['s'],5)
        self.assertEqual(self.device.transcript.decode(),'请检查页面布局。')
        self.assertEqual([c[0] for c in self.calls],['/v1/transcribe'])

    async def test_v2_marks_recognition_only_after_verified_upload(self):
        self.device.meta['v']=2
        await self.pump(lambda:self.device.meta['s']==5)
        self.assertTrue(self.device.processing)
        self.assertEqual(self.device.transcript.decode(),'请检查页面布局。')

    async def test_v2_bad_upload_never_marks_recognition(self):
        self.device.meta['v']=2
        self.device.meta['crc']^=1
        await self.pump(lambda:self.device.meta['s']==8)
        self.assertFalse(self.device.processing)
        self.assertEqual(self.calls,[])

    async def test_v3_uses_larger_authenticated_windows(self):
        self.device.audio=bytes(range(256))*172
        self.device.meta.update(v=3,n=len(self.device.audio),samples=len(self.device.audio)*2,crc=zlib.crc32(self.device.audio))
        await self.pump(lambda:self.device.meta['s']==5)
        self.assertEqual(set(self.device.pull_counts),{96})
        self.assertLessEqual(len(self.device.pull_counts),5)
        self.assertTrue(self.device.processing)
        self.assertEqual(self.device.transcript.decode(),'请检查页面布局。')

    async def test_unsigned_metadata_notification_is_rejected(self):
        from speech_relay import Collector
        self.relay.collector=Collector(self.device.meta,self.device.key,self.device.nonce)
        self.relay.notify(None,json.dumps({'data':self.device.meta,'h':'0'*64}).encode())
        self.assertTrue(self.relay.receive_error)

    async def test_orphaned_send_is_reported_unknown_without_resending(self):
        self.device.meta['s']=6
        await self.pump(lambda:self.device.meta['s']==8)
        self.assertEqual(self.device.statuses,[5])
        self.assertEqual(self.calls,[])

    async def test_no_speech_has_its_own_error_code(self):
        self.patch.stop()
        with patch('speech_relay.local_http',side_effect=SpeechServiceError('no_speech',422)):
            await self.pump(lambda:self.device.meta['s']==8)
        self.assertEqual(self.device.statuses,[6])

    async def test_unknown_delivery_is_not_retried(self):
        await self.pump(lambda:self.device.meta['s']==5)
        self.device.meta['s']=6
        self.patch.stop()
        with patch('speech_relay.local_http',side_effect=TimeoutError) as sender:
            await self.pump(lambda:self.device.meta['s']==8)
            await self.pump(lambda:True)
        self.assertEqual(sender.call_count,1)
        self.assertEqual(self.device.statuses,[1,5])

    async def test_closed_question_is_not_retargeted_or_retried(self):
        await self.pump(lambda:self.device.meta['s']==5)
        self.relay.job['question']='b'*32
        self.device.meta['s']=6
        self.patch.stop()
        with patch('speech_relay.local_http',side_effect=SpeechServiceError('question_no_longer_pending',409)) as sender:
            await self.pump(lambda:self.device.meta['s']==8)
            await self.pump(lambda:True)
        sender.assert_called_once()
        self.assertEqual(sender.call_args.args[0],'/v1/answer')
        self.assertEqual(sender.call_args.args[1]['question'],'b'*32)
        self.assertEqual(self.device.statuses,[1,9])

    async def test_desktop_outage_is_not_claimed_to_be_a_changed_question(self):
        await self.pump(lambda:self.device.meta['s']==5)
        self.device.meta['s']=6
        self.patch.stop()
        with patch('speech_relay.local_http',side_effect=SpeechServiceError('answer_unavailable',503)) as sender:
            await self.pump(lambda:self.device.meta['s']==8)
        sender.assert_called_once()
        self.assertEqual(self.device.statuses,[1,5])


if __name__=='__main__':unittest.main()
