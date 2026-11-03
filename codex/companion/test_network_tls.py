"""Real loopback TLS socket tests with isolated certificates and fixture keys.
Never opens the live gateway, Bluetooth, Hub or any action-delivery endpoint.
"""
import base64
import http.client
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
from network_security import create_gateway, encode, request_proof
from network_gateway import Gateway, Server

class TLS(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=create_gateway(self.root,'192.168.50.10','Fixture')
        self.gateway=Gateway(self.root,self.settings,{'device':'Fixture','key':'01'*32},'fixture-local-token',self.root/'missing-snapshot')
        self.server=Server(('127.0.0.1',0),self.gateway)
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.root/'server-cert.pem',self.root/'server-key.pem');self.server.context=context
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(2);self.temp.cleanup()
    def client(self):
        # Virtual fixture host keeps actual TLS hostname/SAN verification on.
        context=ssl.create_default_context(cafile=str(self.root/'server-cert.pem'))
        server=self.server
        class LocalRoute(http.client.HTTPSConnection):
            def connect(inner):
                raw=socket.create_connection(server.server_address,timeout=2)
                inner.sock=context.wrap_socket(raw,server_hostname='192.168.50.10')
        return LocalRoute('192.168.50.10',timeout=3)
    def post(self,client,path,data,authenticated=True):
        raw=encode(data);headers={'Content-Type':'application/json','X-XQ-Proof':request_proof(self.gateway.key,path,raw) if authenticated else '00'*32}
        client.request('POST',path,raw,headers);r=client.getresponse();return r.status,json.loads(r.read())
    def test_real_tls_hello_and_bad_proof_rejected(self):
        client=self.client()
        try:
            status,result=self.post(client,'/v1/hello',{'device':'Fixture','client':'ab'*16})
            self.assertEqual(status,200);self.assertEqual(len(result['session']),64)
            status,result=self.post(client,'/v1/hello',{'device':'Fixture','client':'ab'*16},False)
            self.assertEqual(status,400);self.assertEqual(result['error'],'device_auth')
        finally:client.close()
    def test_gateway_does_not_expose_hub_or_local_control_routes(self):
        for route in ['/v1/snapshot','/v1/events','/v1/provision','/v1/answer']:
            client=self.client()
            try:
                status,result=self.post(client,route,{})
                self.assertEqual(status,400);self.assertEqual(result['error'],'not_found')
            finally:client.close()
    def test_wrong_hostname_fails_certificate_validation(self):
        context=ssl.create_default_context(cafile=str(self.root/'server-cert.pem'))
        raw=socket.create_connection(self.server.server_address,timeout=2)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):context.wrap_socket(raw,server_hostname='wrong.example')
        finally:raw.close()
    def test_unknown_certificate_is_not_trusted(self):
        raw=socket.create_connection(self.server.server_address,timeout=2)
        try:
            with self.assertRaises(ssl.SSLCertVerificationError):ssl.create_default_context().wrap_socket(raw,server_hostname='192.168.50.10')
        finally:raw.close()

if __name__=='__main__':unittest.main()
