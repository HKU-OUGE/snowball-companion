#!/usr/bin/env python3
"""Authenticated HTTPS RPC gateway for Snowball. Hub stays loopback-only.

The LAN listener exposes ONLY an authenticated bounded device protocol, never
Hub HTTP routes or shell commands. The separate loopback control API accepts
same-host requests using the existing private local token. No access logs.
"""
import argparse
import asyncio
import base64
from collections import deque
import concurrent.futures
import errno
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import ssl
import socket
import threading
import time

from private_files import read_text as read_private_text
from network_security import create_gateway, encode, endpoint, request_proof, validate_config
from bridge import CONFIG, INFO, WRITE, StatusRelay, CatalogRelay, parse_source, SourceUnavailable, read_info, verify_info, decode_info, verify_request
from attention_relay import AttentionRelay, SummaryRelay
from pet_sync import PetRelay
from power_telemetry import PowerRelay, POWER

ROOT = Path.home()/'.config/xueqiu/network'
SOURCE = Path.home()/'.local/state/xueqiu/hub/snapshot.json'


class ProtocolError(ValueError):
    pass


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProtocolError('duplicate_field')
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError('invalid_number')))


class Pipe:
    def __init__(self, pairing):
        self.pairing = pairing
        self.condition = threading.Condition()
        self.session = None
        self.counter = 0
        self.next_id = 0
        self.pending = {}
        self.queue = deque()
        self.last_seen = 0
        self.latest_info = None
        self.telemetry = {}
        self.epoch = 0
        self.poll_inflight = False
        self.remote = False
        self.pending_meta = {}
        self.diagnostics = {'hello_ok': 0, 'poll_ok': 0, 'rpc_acked': 0,
                            'rpc_rejected': 0, 'rpc_timeouts': 0, 'resets': 0}
        self.http_diagnostics = {}

    @property
    def freshness(self):
        return 20.0 if self.remote else 6.0

    @property
    def exchange_timeout(self):
        return 20.0 if self.remote else 6.0

    def fresh(self):
        with self.condition:
            return self.latest_info is not None and time.monotonic()-self.last_seen < self.freshness

    def record_http(self, path, outcome, elapsed, reason=''):
        # A fixed vocabulary only: no URL queries, body, identity, proof, or key.
        name = {'/v1/hello': 'hello', '/v1/poll': 'poll'}.get(path, 'other')
        outcome = outcome if outcome in ('ok', 'rejected', 'io') else 'other'
        reasons = {'body_bounds', 'incomplete', 'device_auth', 'not_found',
                   'device_identity', 'poll_schema', 'device_proof', 'info_too_large',
                   'session_or_replay', 'ack_schema', 'unexpected_ack', 'ack_data',
                   'session_replaced', 'duplicate_field', 'invalid_number'}
        reason = reason if reason in reasons else 'invalid_request' if reason else ''
        with self.condition:
            row = self.http_diagnostics.setdefault(name, {'ok':0, 'rejected':0, 'io':0, 'other':0, 'last_ms':0})
            row[outcome] += 1
            row['last_ms'] = round(max(0, elapsed)*1000)
            if reason:row['last_error'] = reason

    def diagnostic_status(self):
        with self.condition:
            return dict(self.diagnostics, route='remote' if self.remote else 'lan',
                        pending=len(self.pending), queued=len(self.queue),
                        http={name: dict(row) for name, row in self.http_diagnostics.items()})

    def reset(self, epoch=None):
        with self.condition:
            # A cancelled/late old exchange must not tear down a newer hello.
            if epoch is not None and epoch != self.epoch:
                return False
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError('Device transport changed'))
            self.pending.clear()
            self.pending_meta.clear()
            self.queue.clear()
            self.session = None
            self.latest_info = None
            self.last_seen = 0
            self.counter = 0
            self.remote = False
            self.epoch += 1
            self.diagnostics['resets'] += 1
            self.condition.notify_all()
            return True

    def hello(self, data):
        if set(data) != {'device', 'client'} or data['device'] != self.pairing['device'] or not isinstance(data['client'], str) or len(data['client']) != 32:
            raise ProtocolError('device_identity')
        self.reset()
        with self.condition:
            self.session = secrets.token_hex(32)
            self.diagnostics['hello_ok'] += 1
            return {'session': self.session, 'client': data['client']}

    def poll(self, data):
        if set(data) != {'session', 'counter', 'info', 'acks', 'network'} or not isinstance(data['acks'], list) or len(data['acks']) > 16:
            raise ProtocolError('poll_schema')
        try:
            raw_info = base64.b64decode(data['info'], validate=True)
            if len(raw_info) > 768:
                raise ProtocolError('info_too_large')
            verify_info(decode_info(raw_info), self.pairing)
        except (ValueError, TypeError, KeyError) as error:
            raise ProtocolError('device_proof') from error
        with self.condition:
            if not self.session or data['session'] != self.session or type(data['counter']) is not int or data['counter'] != self.counter+1 or self.poll_inflight:
                raise ProtocolError('session_or_replay')
            self.counter = data['counter']
            self.last_seen = time.monotonic()
            self.latest_info = raw_info
            self.telemetry = {k: v for k, v in data['network'].items() if k in ('v','mode','configured','sealed','window','wifi','active','reason','state','ssid','ip','route','failures','polls','rx','tx','age','ota_v')} if isinstance(data['network'], dict) else {}
            # Handler has authenticated the exact poll body before this point.
            # Only the authenticated remote route gets a longer WAN ACK budget.
            self.remote = self.telemetry.get('route') == 'remote'
            self.diagnostics['poll_ok'] += 1
            for ack in data['acks']:
                if not isinstance(ack, dict) or type(ack.get('id')) is not int or type(ack.get('rc')) is not int:
                    raise ProtocolError('ack_schema')
                future = self.pending.pop(ack['id'], None)
                metadata = self.pending_meta.pop(ack['id'], {})
                if future is None:
                    raise ProtocolError('unexpected_ack')
                if ack['rc'] < 0:
                    self.diagnostics['rpc_rejected'] += 1
                    self.diagnostics['last_rejection'] = dict(metadata, rc=ack['rc'])
                    print('Network RPC rejected: '+json.dumps(dict(metadata, rc=ack['rc'],
                          route='remote' if self.remote else 'lan'), separators=(',',':')), flush=True)
                    future.set_exception(OSError('Device rejected RPC'))
                else:
                    try:
                        result = base64.b64decode(ack.get('data',''), validate=True)
                        if len(result) > 768 or (ack['rc'] > 0 and len(result) != ack['rc']):
                            raise ValueError()
                        future.set_result(result)
                        self.diagnostics['rpc_acked'] += 1
                    except (ValueError, TypeError):
                        future.set_exception(ProtocolError('ack_data'))
                        raise ProtocolError('ack_data')
            self.condition.notify_all()
            self.poll_inflight = True
            try:
                # Release the lock while waiting; a state event can wake this
                # held poll immediately. No fixed multi-second device polling.
                self.condition.wait_for(lambda: bool(self.queue) or data['session'] != self.session, timeout=0.8)
                if data['session'] != self.session:
                    raise ProtocolError('session_replaced')
                ops = [self.queue.popleft() for _ in range(min(16, len(self.queue)))]
                return {'counter': self.counter, 'ops': ops}
            finally:
                self.poll_inflight = False

    def exchange(self, ops, epoch):
        futures = []
        with self.condition:
            if epoch != self.epoch or self.latest_info is None or time.monotonic()-self.last_seen > self.freshness:
                raise ConnectionError('Device not connected')
            if len(self.pending)+len(ops) > 32:
                raise ProtocolError('queue_full')
            for op in ops:
                self.next_id += 1
                future = concurrent.futures.Future()
                self.pending[self.next_id] = future
                metadata = {'ch': op.get('ch', 0), 'write': op.get('w') is True}
                if metadata['write']:
                    packet = base64.b64decode(op.get('data', ''), validate=True)
                    metadata['version'] = packet[0] if packet else -1
                self.pending_meta[self.next_id] = metadata
                self.queue.append(dict(op, id=self.next_id))
                futures.append(future)
            self.condition.notify_all()
            timeout = self.exchange_timeout
        try:
            # This waits for a later device poll carrying the applied ACK, not
            # for one HTTP response. WAN exchanges can span two round trips.
            deadline = time.monotonic()+timeout
            return [f.result(timeout=max(.01, deadline-time.monotonic())) for f in futures]
        except Exception as error:
            # Never resend a possibly-applied action. A new session rebuilds
            # only snapshots; outgoing UI requests are invalidated on handoff.
            if isinstance(error, TimeoutError):
                with self.condition:self.diagnostics['rpc_timeouts'] += 1
            self.reset(epoch)
            raise


class RemoteGatt:
    transport = "wifi"
    def __init__(self, pipe):
        self.pipe, self.epoch = pipe, pipe.epoch
        self.writes = []
        self.services = self

    @property
    def io_timeout(self):
        return 24.0 if self.pipe.remote else 8.0

    def get_characteristic(self, uuid):
        return uuid if uuid in (INFO, WRITE, POWER) else None

    async def flush(self, read=None):
        ops, self.writes = self.writes, []
        if read:
            ops.append({'ch': int(read[4:8],16), 'w': False})
        if not ops:
            return []
        try:
            return await asyncio.to_thread(self.pipe.exchange, ops, self.epoch)
        except asyncio.CancelledError:
            # to_thread cannot cancel its waiting thread. Invalidate the pipe
            # immediately so queued actions never execute after cancellation.
            self.pipe.reset(self.epoch)
            raise

    async def read_gatt_char(self, uuid):
        # This is the actual signed INFO delivered by the latest device poll.
        # Writes always use an explicit device read barrier, never a guessed ACK.
        if uuid == INFO and not self.writes and self.epoch == self.pipe.epoch and self.pipe.latest_info and time.monotonic()-self.pipe.last_seen < .4:
            return self.pipe.latest_info
        return (await self.flush(uuid))[-1]

    async def write_gatt_char(self, uuid, packet, response=True):
        if uuid != WRITE or not 47 <= len(packet) <= 272 or (packet[0] > 7 and not (packet[0]==10 and packet[2]==6)):
            raise ProtocolError('unsupported_network_operation')
        self.writes.append({'ch': 3, 'w': True, 'data': base64.b64encode(packet).decode()})
        if len(self.writes) >= 12:
            await self.flush()


class Gateway:
    def __init__(self, root, settings, pairing, token, source):
        self.root, self.settings, self.pairing, self.token = root, settings, pairing, token
        self.key = bytes.fromhex(settings['access'])
        self.source = source
        self.pipe = Pipe(pairing)
        self.lock = threading.Lock()
        self.job = None
        self.job_at = 0
        self.job_taken = False
        self.result = 'idle'
        self.ble_status = {}
        self.ble_at = 0
        self.last_hello = 0
        self.sync_state = 'waiting'
        self.last_synced = 0

    def status(self):
        with self.lock:
            network_online = self.pipe.fresh()
            ble_fresh = time.monotonic()-self.ble_at < 8
            status = dict(self.pipe.telemetry if network_online else self.ble_status if ble_fresh else {})
            return {'gateway': True, 'lan': f'https://{self.settings["bind"]}:{self.settings["port"]}',
                    'device': self.pairing['device'], 'ble': ble_fresh,
                    'online': network_online, 'network': status,
                    'provision': self.result, 'sync': self.sync_state,
                    'last_synced': self.last_synced or None,
                    'diagnostics': self.pipe.diagnostic_status(),
                    'remote': 'requires_reachable_https_gateway'}

    def provision(self, data):
        if not isinstance(data, dict) or set(data) != {'ssid','password','mode','remote'}:
            raise ProtocolError('provision_schema')
        with self.lock:
            if time.monotonic()-self.ble_at >= 8 or not self.ble_status.get('window'):
                raise ProtocolError('open_device_network_page')
            if self.job and time.monotonic()-self.job_at < 60:
                raise ProtocolError('provision_busy')
            config = {'v':1, 'mode':data['mode'], 'ssid':data['ssid'], 'password':data['password'],
                      'lan':f'https://{self.settings["bind"]}:{self.settings["port"]}',
                      'remote': endpoint(data['remote']) if data['remote'] else '',
                      'ca':(self.root/'server-cert.pem').read_text(), 'device':self.pairing['device'],
                      'access':self.settings['access'], 'issued':int(time.time())}
            validate_config(config)
            self.job, self.job_at, self.job_taken = config, time.monotonic(), False
            self.result = 'queued'
            return {'ok':True}

    def bridge(self, data):
        if not isinstance(data,dict) or set(data) != {'status'} or not isinstance(data['status'],dict):
            raise ProtocolError('bridge_schema')
        with self.lock:
            self.ble_status, self.ble_at = data['status'], time.monotonic()
            if self.job and time.monotonic()-self.job_at > 60:
                self.job.clear(); self.job = None; self.result = 'expired'
            if not self.job or self.job_taken:
                return {}
            self.job_taken = True
            return {'config': self.job.copy()}

    def finish(self, data):
        allowed={'stored','temporary','failed','open_device_network_page','device_mismatch'}
        if not isinstance(data,dict) or data.get('result') not in allowed:
            raise ProtocolError('result_schema')
        with self.lock:
            self.result=data['result']
            if isinstance(data.get('status'),dict):self.ble_status=data['status']
            if self.job:self.job.clear()
            self.job=None
        return {'ok':True}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8
    allow_reuse_address = os.name != 'nt'

    def server_bind(self):
        if os.name == 'nt':
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()
    def __init__(self, address, gateway, control=False):
        self.gateway, self.control = gateway, control
        self.slots = threading.BoundedSemaphore(6)
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock,address

    def process_request(self, request, client_address):
        if not self.slots.acquire(False):
            self.shutdown_request(request);return
        super().process_request(request,client_address)

    def process_request_thread(self, request, client_address):
        try:
            if not self.control:
                try:request=self.context.wrap_socket(request,server_side=True)
                except (ssl.SSLError,OSError):request.close();return
            super().process_request_thread(request,client_address)
        finally:self.slots.release()


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    server_version='SnowballGateway/1'
    def log_message(self,*args):pass

    def reply(self,code,value):
        body=encode(value)
        self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)

    def local_auth(self):
        g=self.server.gateway
        if self.client_address[0]!='127.0.0.1' or self.headers.get('Origin') or self.headers.get('Host') not in ('127.0.0.1:4194','localhost:4194'):
            raise ProtocolError('local_only')
        headers=self.headers.get_all('Authorization',[])
        if len(headers)!=1 or not hmac.compare_digest(headers[0], 'Bearer '+g.token):raise ProtocolError('unauthorized')

    def do_GET(self):
        try:
            if not self.server.control or self.path!='/v1/status':raise ProtocolError('not_found')
            self.local_auth();self.reply(200,self.server.gateway.status())
        except ProtocolError:self.reply(403,{'error':'request_rejected'})

    def do_POST(self):
        started=time.monotonic();outcome='rejected';reason=''
        try:
            lengths=self.headers.get_all('Content-Length',[])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit() or not 2<=int(lengths[0])<=18000 or self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ProtocolError('body_bounds')
            if self.server.control:self.local_auth()
            raw=self.rfile.read(int(lengths[0]))
            if len(raw)!=int(lengths[0]):raise ProtocolError('incomplete')
            g=self.server.gateway
            if not self.server.control:
                proofs=self.headers.get_all('X-XQ-Proof',[])
                if len(proofs)!=1 or not hmac.compare_digest(request_proof(g.key,self.path,raw),proofs[0]):raise ProtocolError('device_auth')
            data=strict_json(raw)
            if self.server.control:
                action={'/v1/provision':g.provision,'/v1/bridge':g.bridge,'/v1/result':g.finish}.get(self.path)
            else:action={'/v1/hello':g.pipe.hello,'/v1/poll':g.pipe.poll}.get(self.path)
            if not action:raise ProtocolError('not_found')
            self.reply(200,action(data))
            outcome='ok'
        except (ValueError,TypeError,KeyError) as error:
            reason=str(error) if isinstance(error,ProtocolError) else 'invalid_request'
            self.reply(400,{'error':str(error) if isinstance(error,ProtocolError) else 'invalid_request'})
            self.close_connection=True
        except (OSError,TimeoutError):outcome='io';self.close_connection=True
        finally:
            if not self.server.control:self.server.gateway.pipe.record_http(self.path,outcome,time.monotonic()-started,reason)


async def relay(g):
    while True:
        if not g.pipe.fresh():
            g.sync_state='waiting';await asyncio.sleep(.5);continue
        client=RemoteGatt(g.pipe)
        catalogue=CatalogRelay();attention=AttentionRelay(read_info,verify_request);summaries=SummaryRelay(read_info,catalogue.catalogue);power=PowerRelay();pets=PetRelay(io_timeout=client.io_timeout if g.pipe.remote else 6);states=StatusRelay()
        try:
            while client.epoch==g.pipe.epoch:
                payload=None;target=None
                try:payload=json.loads(read_private_text(g.source));target=parse_source(payload)
                except (OSError,ValueError):pass
                # Establish the authenticated device clock before a potentially
                # large catalogue transfer. OTA HTTPS proofs use that clock.
                await power.step(client,g.pairing,read_info,payload,target)
                refreshed=await catalogue.step(client,g.pairing,payload)
                if refreshed is not None:payload=refreshed;states.previous=None
                refreshed=await attention.step(client,g.pairing,payload)
                if refreshed is not None:payload=refreshed
                try:target=parse_source(payload)
                except (ValueError,TypeError):target=None
                await summaries.step(client,g.pairing,payload)
                await pets.step(client,g.pairing,payload,read_info)
                ack=await states.step(client,g.pairing,target)
                await client.flush()
                if ack is not None:g.last_synced=time.time()
                g.sync_state='synced' if target else 'source_unavailable'
                await asyncio.sleep(.2)
        except Exception as error:
            g.sync_state='reconnecting'
            print('Network relay retry: '+type(error).__name__,flush=True)
            print('Network relay diagnostics: '+json.dumps(g.pipe.diagnostic_status(),separators=(',',':')),flush=True)
            g.pipe.reset(client.epoch)
            await asyncio.sleep(1)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=ROOT);parser.add_argument('--bind');parser.add_argument('--source',type=Path,default=SOURCE);parser.add_argument('--init',action='store_true')
    args=parser.parse_args();pairing=json.loads(read_private_text(CONFIG))
    settings=create_gateway(args.root,args.bind,pairing['device']) if args.init else json.loads(read_private_text(args.root/'gateway.json'))
    if settings.get('device') != pairing.get('device'):
        raise SystemExit('Saved gateway belongs to another device; explicit migration is required.')
    token=read_private_text(Path.home()/'.config/xueqiu/hub-token').strip()
    g=Gateway(args.root,settings,pairing,token,args.source)
    try:
        lan=Server((settings['bind'],settings['port']),g)
    except OSError as error:
        if error.errno!=errno.EADDRNOTAVAIL and getattr(error,'winerror',None)!=10049:raise
        # A saved DHCP address can disappear while the service is running.
        # Preserve the local control/cloud relay without opening a public LAN
        # listener or changing credentials/certificates behind the user's back.
        print('Saved LAN address unavailable; starting loopback-only gateway',flush=True)
        lan=Server(('127.0.0.1',settings['port']),g)
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2;context.load_cert_chain(args.root/'server-cert.pem',args.root/'server-key.pem');lan.context=context
    control=Server(('127.0.0.1',4194),g,True)
    for s in (lan,control):threading.Thread(target=s.serve_forever,daemon=True).start()
    print('Snowball gateway ready; Hub remains loopback-only',flush=True)
    try:asyncio.run(relay(g))
    finally:
        g.pipe.reset()
        for s in (lan,control):s.shutdown();s.server_close()

if __name__=='__main__':main()
