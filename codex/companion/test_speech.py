import asyncio,base64,ctypes,hashlib,hmac,json,math,struct,subprocess,tempfile,time,unittest,zlib
from pathlib import Path
from unittest.mock import Mock,patch
from speech_relay import Collector,parse_meta,bound_target,local_http,SpeechServiceError
from status_hub import StatusHub,Invalid
from codex_desktop import DeliveryError

class SpeechProtocol(unittest.TestCase):
    def setUp(self):
        self.key=b'k'*32;self.nonce=b'n'*8
        self.meta={'v':1,'id':23,'s':3,'p':0,'sid':'1'*32,'qid':'','n':4000,'samples':8000,'crc':0,'busy':0}
    def envelope(self,d):
        raw=json.dumps(d,separators=(',',':')).encode()
        return json.dumps({'data':d,'h':hmac.digest(self.key,b'XQVOICE'+self.nonce+raw,'sha256').hex()}).encode()
    def packet(self,offset,raw,identity=23):
        body=b'XQVC'+struct.pack('<II',identity,offset)+raw
        return body+hmac.digest(self.key,self.nonce+body,'sha256')[:16]
    def test_metadata_signature_nonce_bounds(self):
        self.assertEqual(parse_meta(self.envelope(self.meta),self.key,self.nonce),self.meta)
        with self.assertRaises(ValueError):parse_meta(self.envelope(self.meta),self.key,b'x'*8)
        for patch in [{'samples':320001},{'n':-1},{'p':1},{'sid':'../bad'},{'s':True},{'n':1}]:
            with self.assertRaises(ValueError):parse_meta(self.envelope({**self.meta,**patch}),self.key,self.nonce)
    def test_out_of_order_duplicates_missing_crc(self):
        raw=bytes(range(200))*20;self.meta['crc']=zlib.crc32(raw)
        c=Collector(self.meta,self.key,self.nonce)
        for at in reversed(range(0,4000,160)):c.receive(self.packet(at,raw[at:at+160]))
        self.assertTrue(c.complete());c.receive(self.packet(0,raw[:160]));self.assertTrue(c.complete())
        with self.assertRaises(ValueError):c.receive(self.packet(0,b'x'))
        missing=Collector(self.meta,self.key,self.nonce);missing.receive(self.packet(160,raw[160:320]));self.assertFalse(missing.complete());self.assertEqual(missing.at,0)
    def test_tampered_packet_and_other_recording(self):
        c=Collector(self.meta,self.key,self.nonce)
        with self.assertRaises(ValueError):c.receive(self.packet(0,b'abc')[:-1]+b'x')
        c.receive(self.packet(0,b'abc',24));self.assertEqual(c.at,0)
        with self.assertRaises(ValueError):c.receive(self.packet(3999,b'abc'))
    def test_exact_session_and_question_binding(self):
        identity='codex-hooks/'+'a'*32;meta={**self.meta,'sid':hashlib.sha256(identity.encode()).hexdigest()[:32]}
        p={'hub_alive':True,'generated_at':time.time(),'sessions':[{'source':'codex-hooks','session_id':'a'*32,'provider':'Codex','questions':[]}]}
        self.assertEqual(bound_target(p,meta),identity)
        with self.assertRaises(ValueError):bound_target(p,{**meta,'qid':'b'*32})
        with self.assertRaises(ValueError):bound_target({**p,'generated_at':time.time()-30},meta)
        with self.assertRaises(ValueError):bound_target(p,{**meta,'sid':'c'*32})

class Messages(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.desktop=Mock();self.desktop.available.return_value=True;self.desktop.instruction_route.return_value={'thread':'real'};self.desktop.summaries={}
        self.hub=StatusHub(Path(self.tmp.name),desktop=self.desktop)
        self.hub.accept({'version':1,'provider':'Codex','source':'codex-hooks','session_id':'a'*32,'seq':1,'event_id':'a','timestamp':time.time(),'kind':'task','phase':'idle'})
        self.request={'session':'codex-hooks/'+'a'*32,'text':'检查这个页面的布局','request_id':'b'*32}
    def test_explicit_message_once_exact_target_no_plaintext_log(self):
        self.assertEqual(self.hub.message(self.request)['delivery'],'accepted');self.hub.message(self.request)
        self.desktop.deliver_instruction.assert_called_once_with(self.request['session'],self.request['text'],self.request['request_id'])
        self.assertNotIn(self.request['text'],self.hub.message_path.read_text())
        with self.assertRaises(Invalid):self.hub.message({**self.request,'text':'changed'})
    def test_unconfirmed_never_replayed_after_retry_or_restart(self):
        self.desktop.deliver_instruction.side_effect=DeliveryError('timeout',True)
        self.assertEqual(self.hub.message(self.request)['delivery'],'unconfirmed')
        restarted=StatusHub(Path(self.tmp.name),desktop=self.desktop)
        self.assertEqual(restarted.message(self.request)['delivery'],'unconfirmed');self.desktop.deliver_instruction.assert_called_once()
    def test_reject_unknown_or_unsupported(self):
        for patch in [{'session':'missing/x'},{'text':'\x00'},{'request_id':'not-id'},{'approve':True}]:
            with self.assertRaises(Invalid):self.hub.message({**self.request,**patch})
        self.desktop.deliver_instruction.assert_not_called()

@unittest.skipUnless((Path(__file__).resolve().parents[1] / 'main/speech_adpcm.h').is_file(), "Private firmware parity test; firmware is not part of the plugin distribution")
class AdpcmParity(unittest.TestCase):
    def test_native_encoder_matches_standard_reference(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d:
            src=Path(d)/'codec.c';lib=Path(d)/'codec.so'
            src.write_text('#include "speech_adpcm.h"\nvoid encode(const int16_t *in,uint8_t*out,int n){xqsp_adpcm_t s={0};for(int i=0;i<n;i++){unsigned b=xqsp_adpcm(&s,in[i]);if(i%2)out[i/2]|=b;else out[i/2]=b<<4;}}')
            subprocess.run(['cc','-shared','-fPIC','-I',str(root/'main'),str(src),'-o',str(lib)],check=True)
            pcm=struct.pack('<8000h',*(int(12000*math.sin(i*.05)+4000*math.sin(i*.127)) for i in range(8000)))
            native=ctypes.CDLL(str(lib));samples=(ctypes.c_int16*8000).from_buffer_copy(pcm);out=(ctypes.c_uint8*4000)();native.encode(samples,out,8000)
            # Frozen independent oracle: CPython 3.12 audioop.lin2adpcm(pcm,2,None).
            # audioop was removed in Python 3.13, also used by the BLE runtime.
            self.assertEqual(hashlib.sha256(bytes(out)).hexdigest(),
                '47bf55945f6750dcf7e4fcfae5708f3781e0b98dcc9446545bfd004a6f7c9c2a')

class HttpAcknowledgements(unittest.TestCase):
    def test_large_hub_snapshot_does_not_hide_accepted_answer(self):
        body={'delivery':'accepted','snapshot':{'sessions':'x'*75000}}
        response=Mock(status=200);response.read.side_effect=lambda n:json.dumps(body).encode()[:n]
        connection=Mock();connection.getresponse.return_value=response
        with patch('speech_relay.Path.read_text',return_value='test-token'),patch('speech_relay.http.client.HTTPConnection',return_value=connection):
            self.assertEqual(local_http('/v1/answer',{},4190)['delivery'],'accepted')
        connection.request.assert_called_once()
        response.read.assert_called_once_with(1024*1024+1)

    def test_asr_response_remains_bounded(self):
        response=Mock(status=200);response.read.return_value=b'x'*65537
        connection=Mock();connection.getresponse.return_value=response
        with patch('speech_relay.Path.read_text',return_value='test-token'),patch('speech_relay.http.client.HTTPConnection',return_value=connection):
            with self.assertRaises(ValueError):local_http('/v1/transcribe',{},4191)

    def test_hub_rejection_is_not_reported_as_asr_outage(self):
        response=Mock(status=409)
        response.read.return_value=b'{"error":"question_no_longer_pending"}'
        connection=Mock();connection.getresponse.return_value=response
        with patch('speech_relay.Path.read_text',return_value='test-token'),patch('speech_relay.http.client.HTTPConnection',return_value=connection):
            with self.assertRaises(SpeechServiceError) as result:local_http('/v1/answer',{},4190)
        self.assertEqual((result.exception.code,result.exception.status),('question_no_longer_pending',409))
        connection.request.assert_called_once()

if __name__=='__main__':unittest.main()
