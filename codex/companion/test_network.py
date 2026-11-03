"""Offline security/transport tests: no radio, credentials or live Hub actions."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from network_security import *
from bridge import WRITE
from network_gateway import Pipe, RemoteGatt, ProtocolError, Gateway, strict_json

KEY=bytes(range(32))
NONCE='1122334455667788'
INFO_FIXTURE={'accepted':7,'nonce':NONCE,'device':'Fixture'}

def config():
    return {'v':1,'mode':1,'ssid':'测试网络','password':'fixture-secret','lan':'https://192.168.9.20:4193','remote':'','ca':'-----BEGIN CERTIFICATE-----\n'+'x'*160,'device':'Fixture','access':'ab'*32,'issued':1800000000}

class Security(unittest.TestCase):
    def test_config_sealed_and_bound_to_nonce(self):
        c=config();blob=seal_config(c,KEY,NONCE)
        self.assertNotIn(c['password'].encode(),blob)
        nonce=bytes.fromhex(NONCE);key=hmac.digest(KEY,b'XQ-PROVISION-v1'+nonce,'sha256')
        self.assertEqual(json.loads(AESGCM(key).decrypt(blob[:12],blob[12:],b'XQNET1'+nonce)),c)
        with self.assertRaises(Exception):AESGCM(key).decrypt(blob[:12],blob[12:-1]+bytes([blob[-1]^1]),b'XQNET1'+nonce)
        with self.assertRaises(Exception):AESGCM(key).decrypt(blob[:12],blob[12:],b'XQNET1'+b'0'*8)
    def test_packets_contiguous_bounded_signed(self):
        blob=seal_config(config(),KEY,NONCE)
        rows=list(packets(blob,KEY,INFO_FIXTURE));self.assertEqual(rows[0][2],0);self.assertEqual(rows[-1][2],2)
        rebuilt=b''
        for index,p in enumerate(rows,8):
            self.assertLessEqual(len(p),272);self.assertEqual(struct.unpack_from('<I',p,3)[0],index)
            self.assertTrue(hmac.compare_digest(hmac.digest(KEY,p[:-32],'sha256'),p[-32:]))
            if p[2]==1:self.assertEqual(struct.unpack_from('<H',p,15)[0],len(rebuilt));rebuilt+=p[17:-32]
        self.assertEqual(rebuilt,blob)
    def test_plain_http_and_embedded_credentials_rejected(self):
        for value in ['http://192.168.1.1:4193','https://a:b@host:4193','https://host:4193/path','https://host:4193?secret=x','https://host:4193/#x']:
            with self.subTest(value=value),self.assertRaises(ValueError):endpoint(value)
    def test_limits_and_unknown_fields(self):
        for key,value in [('ssid','中'*11),('password','short'),('mode',True),('access','00'),('issued',0)]:
            c=config();c[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):validate_config(c)
        c=config();c['token']='not_allowed'
        with self.assertRaises(ValueError):validate_config(c)
    def test_duplicate_and_nan_rejected(self):
        for value in ['{"mode":1,"mode":2}','{"a":NaN}']:
            with self.assertRaises(ValueError):strict_json(value)
    def test_private_certificate_san_and_no_silent_rotation(self):
        from cryptography import x509
        with tempfile.TemporaryDirectory() as d:
            a=create_gateway(Path(d),'192.168.9.20','Fixture');b=create_gateway(Path(d),'192.168.9.20','Fixture');self.assertEqual(a,b)
            self.assertEqual(os.stat(Path(d)/'gateway.json').st_mode&0o777,0o600)
            cert=x509.load_pem_x509_certificate((Path(d)/'server-cert.pem').read_bytes())
            self.assertIn('192.168.9.20',str(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value))
            with self.assertRaises(ValueError):create_gateway(Path(d),'192.168.9.21','Fixture')
    def test_request_proof_binds_endpoint_and_body(self):
        proof=request_proof(KEY,'/v1/poll',b'{}')
        self.assertNotEqual(proof,request_proof(KEY,'/v1/hello',b'{}'))
        self.assertNotEqual(proof,request_proof(KEY,'/v1/poll',b'{ }'))

class Transport(unittest.TestCase):
    def setUp(self):
        self.pipe=Pipe({'device':'Fixture'})
        self.session=self.pipe.hello({'device':'Fixture','client':'0'*32})['session']
    def poll(self,counter,acks=None):
        return {'session':self.session,'counter':counter,'info':base64.b64encode(b'{}').decode(),'acks':acks or [],'network':{}}
    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_replay_and_wrong_session_rejected(self,*_):
        self.pipe.poll(self.poll(1))
        with self.assertRaises(ProtocolError):self.pipe.poll(self.poll(1))
        p=self.poll(2);p['session']='bad'
        with self.assertRaises(ProtocolError):self.pipe.poll(p)
    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_no_fake_ack_and_error_propagation(self,*_):
        self.pipe.poll(self.poll(1))
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(self.pipe.exchange,[{'ch':3,'w':True,'data':'AA=='}],self.pipe.epoch)
            reply=self.pipe.poll(self.poll(2));self.assertFalse(pending.done())
            try:self.pipe.poll(self.poll(3,[{'id':reply['ops'][0]['id'],'rc':-128}]))
            except ProtocolError:pass  # Failed RPC invalidates the transport concurrently.
            with self.assertRaises(OSError):pending.result()
    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_read_ack_returns_actual_bytes(self,*_):
        self.pipe.poll(self.poll(1))
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(self.pipe.exchange,[{'ch':2,'w':False}],self.pipe.epoch)
            reply=self.pipe.poll(self.poll(2))
            self.pipe.poll(self.poll(3,[{'id':reply['ops'][0]['id'],'rc':3,'data':'YWJj'}]))
            self.assertEqual(pending.result(),[b'abc'])
    def test_reset_invalidates_old_writer(self):
        epoch=self.pipe.epoch;self.pipe.reset()
        with self.assertRaises(ConnectionError):self.pipe.exchange([{'ch':2,'w':False}],epoch)
    def test_remote_cannot_provision_or_record(self):
        async def run():
            client=RemoteGatt(self.pipe)
            for version in (8,9):
                with self.assertRaises(ProtocolError):await client.write_gatt_char(WRITE,bytes([version])+bytes(46))
        asyncio.run(run())
    def test_provision_requires_fresh_physical_window(self):
        with tempfile.TemporaryDirectory() as d:
            g=Gateway(Path(d),{'access':'ab'*32,'bind':'192.168.9.20','port':4193},{'device':'Fixture'},'token',Path(d)/'no')
            with self.assertRaises(ProtocolError):g.provision({'ssid':'test','password':'fixture-secret','mode':1,'remote':''})
    def test_bad_device_info_rejected_before_commands(self):
        with self.assertRaises(ProtocolError):self.pipe.poll(self.poll(1))

    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_remote_ack_can_take_more_than_six_seconds_without_replay(self,*_):
        data=self.poll(1);data['network']={'route':'remote'};self.pipe.poll(data)
        self.assertEqual(self.pipe.freshness,20)
        self.assertEqual(RemoteGatt(self.pipe).io_timeout,24)
        with ThreadPoolExecutor() as pool:
            pending=pool.submit(self.pipe.exchange,[{'ch':3,'w':True,'data':'Aw=='}],self.pipe.epoch)
            data=self.poll(2);data['network']={'route':'remote'}
            reply=self.pipe.poll(data)
            # An ACK needs a later WAN poll, not merely this HTTP response.
            time.sleep(6.15)
            self.assertFalse(pending.done())
            data=self.poll(3,[{'id':reply['ops'][0]['id'],'rc':0}]);data['network']={'route':'remote'}
            self.pipe.poll(data)
            self.assertEqual(pending.result(timeout=1),[b''])
            self.assertEqual(self.pipe.diagnostics['rpc_acked'],1)
            self.assertFalse(self.pipe.queue)
            self.assertFalse(self.pipe.pending)

    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_longer_freshness_requires_authenticated_remote_poll(self,*_):
        data=self.poll(1);data['network']={'route':'remote'}
        with patch('network_gateway.verify_info',side_effect=ValueError('bad proof')):
            with self.assertRaises(ProtocolError):self.pipe.poll(data)
        self.assertFalse(self.pipe.remote)
        self.pipe.poll(data)
        self.pipe.last_seen=time.monotonic()-7
        self.assertTrue(self.pipe.fresh())
        self.pipe.last_seen=time.monotonic()-21
        self.assertFalse(self.pipe.fresh())
        data=self.poll(2);data['network']={'route':'lan'};self.pipe.poll(data)
        self.assertEqual(RemoteGatt(self.pipe).io_timeout,8)
        self.assertEqual(self.pipe.exchange_timeout,6)
        self.pipe.last_seen=time.monotonic()-7
        self.assertFalse(self.pipe.fresh())

    @patch('network_gateway.verify_info')
    @patch('network_gateway.decode_info')
    def test_cancelled_flush_discards_action_and_old_reset_cannot_destroy_new_session(self,*_):
        self.pipe.poll(self.poll(1))
        old_epoch=self.pipe.epoch
        async def exercise():
            client=RemoteGatt(self.pipe)
            await client.write_gatt_char(WRITE,bytes([3])+bytes(46))
            flush=asyncio.create_task(client.flush())
            for _ in range(100):
                if self.pipe.pending:break
                await asyncio.sleep(.01)
            self.assertTrue(self.pipe.pending)
            flush.cancel()
            with self.assertRaises(asyncio.CancelledError):await flush
            self.assertFalse(self.pipe.pending)
            self.assertFalse(self.pipe.queue)
            self.session=self.pipe.hello({'device':'Fixture','client':'1'*32})['session']
            self.assertFalse(self.pipe.reset(old_epoch))
            self.assertEqual(self.pipe.session,self.session)
            # Allow the abandoned worker to receive its reset exception. It
            # must not reset the newer hello a second time.
            await asyncio.sleep(.03)
            self.assertEqual(self.pipe.session,self.session)
        asyncio.run(exercise())

    def test_diagnostics_keep_only_fixed_path_codes_and_numeric_metadata(self):
        secret='never-record-this-question-or-key'
        self.pipe.record_http('/v1/poll?secret='+secret,'ok',.125)
        self.pipe.record_http('/v1/hello','rejected',.025)
        self.pipe.record_http('/v1/poll','rejected',.125,secret)
        record=self.pipe.diagnostic_status()
        self.assertNotIn(secret,json.dumps(record))
        self.assertEqual(record['http']['other']['ok'],1)
        self.assertEqual(record['http']['hello']['rejected'],1)
        self.assertEqual(record['http']['other']['last_ms'],125)
        self.assertEqual(record['http']['poll']['last_error'],'invalid_request')

if __name__=='__main__':unittest.main()
