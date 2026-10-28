"""Authenticated posted-question display and reminder controls over existing BLE."""
import asyncio
import hashlib
import hmac
import http.client
import json
from pathlib import Path
import struct
import time

from attention_model import QID, clean_question

ACTIONS = {3: 'snooze', 4: 'ignore', 5: 'restore'}
STATUS = {'active': 0, 'snoozed': 1, 'ignored': 2}
PROVIDERS = ['Codex', 'Claude', 'Cursor']
WRITE = '9e7a0003-6ed4-4b6b-bf37-43dc42838c20'


def attention_http(request):
    connection = http.client.HTTPConnection('127.0.0.1', 4190, timeout=12)
    try:
        token = (Path.home() / '.config/xueqiu/hub-token').read_text().strip()
        connection.request('POST', '/v1/answer' if 'request_id' in request else '/v1/attention', json.dumps(request),
            {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
        if response.status != 200:
            raise ValueError('Question no longer pending or reminder action rejected')
        return json.loads(raw)
    finally:
        connection.close()


def rows_for(payload, provider, now):
    if (not isinstance(payload, dict) or not payload.get('hub_alive') or
            not now - 30 < payload.get('generated_at', 0) <= now + 5):
        return [], 0
    rows = []
    for entry in sorted(payload.get('sessions', []), key=lambda s: s.get('priority', 0)):
        if entry.get('provider') != provider:
            continue
        for q in entry.get('questions', []):
            if (not QID.fullmatch(str(q.get('id', ''))) or q.get('status') not in STATUS or
                    q.get('text') != clean_question(q.get('text'))):
                continue
            rows.append({**q, 'session_wire': hashlib.sha256((entry['source'] + '/' + entry['session_id']).encode()).hexdigest()[:32], 'session': entry['source'] + '/' + entry['session_id']})
    # Active questions first; quiet questions remain available for restoration.
    rows.sort(key=lambda q: q['status'] != 'active')
    return rows[:8], len(rows)


def question_packets(generation, rows, total, ack=0, result=0, choices=False):
    yield 0, struct.pack('<IBHIB', generation, len(rows), total, ack, result)
    for index, row in enumerate(rows):
        text = row['text'].encode()
        for offset in range(0, len(text), 180):
            yield 1, struct.pack('<IB16s16sBHH', generation, index,
                bytes.fromhex(row['session_wire']), bytes.fromhex(row['id']),
                STATUS[row['status']], len(text), offset) + text[offset:offset + 180]
        if choices:
            options = row.get('options', [])
            yield 4, struct.pack('<IBBB', generation, index, len(options), bool(row.get('answerable')))
            for option, label in enumerate(options):
                raw = label.encode()
                for offset in range(0,len(raw),180):
                    yield 5, struct.pack('<IBBHH',generation,index,option,len(raw),offset)+raw[offset:offset+180]
    yield 2, struct.pack('<I', generation)


class AttentionRelay:
    def __init__(self, read_info=None, verify_request=None):
        self.read_info, self.verify_request = read_info, verify_request
        self.nonce = None
        self.generations = [0] * 3
        self.fingerprints = [None] * 3
        self.sent_at = [float('-inf')] * 3
        self.sent = {}
        self.last_poll = float('-inf')
        self.handled = None
        self.results = {}

    async def step(self, client, config, payload):
        # Production injects functions from the running bridge (__main__).
        # Importing bridge again would create distinct exception classes and
        # turn a retryable truncated read into a fatal authentication failure.
        if self.read_info is None:
            from bridge import read_info, verify_request
        else:
            read_info, verify_request = self.read_info, self.verify_request
        tick = time.monotonic()
        if tick - self.last_poll < 1:
            return None
        self.last_poll = tick
        info = await read_info(client, config)
        if info.get('attention_v') not in (1, 2):
            return None
        if info['nonce'] != self.nonce:
            self.nonce = info['nonce']
            self.sent_at = [float('-inf')] * 3
            self.fingerprints = [None] * 3
            self.sent.clear()
            self.handled = None
        request = verify_request(info, config)
        refreshed = None
        if (request and request['pending'] and request['action'] in range(3,12) and
                self.handled != (self.nonce, request['seq'])):
            self.handled = (self.nonce, request['seq'])
            p = request['provider']
            result = 2
            try:
                sent = self.sent.get((p, request['generation']))
                if not sent or tick - sent['at'] >= 20:
                    raise ValueError('Question view expired')
                row = next((q for q in sent['rows'] if q['id'] == request['before'] and q['session_wire'] == request['id']), None)
                if not row:
                    raise ValueError('Question identity mismatch')
                current = verify_request(await read_info(client, config), config)
                if not current or not current['pending'] or current['seq'] != request['seq']:
                    raise ValueError('Question action expired')
                if request['action'] >= 6:
                    choice = request['action']-6
                    if not row.get('answerable') or choice >= len(row.get('options', [])):
                        raise ValueError('Option unavailable')
                    rid = hashlib.sha256((self.nonce+':'+str(request['seq'])).encode()).hexdigest()[:32]
                    response = await asyncio.to_thread(attention_http, {'session': row['session'],
                        'question': row['id'], 'choice':choice, 'request_id':rid})
                    refreshed = response.get('snapshot')
                    result = 1 if response.get('delivery') == 'accepted' else 2
                else:
                    refreshed = await asyncio.to_thread(attention_http, {'session': row['session'],
                        'question': row['id'], 'action': ACTIONS[request['action']]})
                    result = 1
            except (OSError, ValueError, TimeoutError, http.client.HTTPException):
                pass
            self.results[p] = (request['seq'], result)
            self.sent_at[p] = float('-inf')
            if refreshed is not None:
                payload = refreshed
        for p, provider in enumerate(PROVIDERS):
            rows, total = rows_for(payload, provider, time.time())
            fingerprint = tuple((q['session'], q['id'], q['text'], q['status'], tuple(q.get('options',[])), bool(q.get('answerable'))) for q in rows), total
            changed = fingerprint != self.fingerprints[p]
            if not changed and tick - self.sent_at[p] < 8:
                continue
            if changed:
                self.generations[p] += 1
            gen = self.generations[p]
            ack, result = self.results.get(p, (0, 0))
            packets = question_packets(gen, rows, total, ack, result, choices=info.get('attention_v')==2) if changed else [(3, struct.pack('<IIB', gen, ack, result))]
            info = await read_info(client, config)
            for op, body in packets:
                head = struct.pack('<BBBI8s', 6, p, op, info['accepted'] + 1, bytes.fromhex(info['nonce'])) + body
                packet = head + hmac.digest(bytes.fromhex(config['key']), head, 'sha256')
                if len(packet) > 272:
                    raise ValueError('Question packet exceeds ATT limit')
                await asyncio.wait_for(client.write_gatt_char(WRITE, packet, response=True), 8)
                info = dict(info, accepted=info['accepted'] + 1)
            self.sent_at[p] = time.monotonic()
            self.fingerprints[p] = fingerprint
            self.sent[(p, gen)] = {'at': self.sent_at[p], 'rows': rows}
            self.sent = {k: v for k, v in self.sent.items() if self.sent_at[p] - v['at'] < 20}
        return refreshed


class SummaryRelay:
    """Separate leased text snapshots; never extends task freshness or changes focus."""
    def __init__(self, read_info, catalogue):
        self.read_info, self.catalogue = read_info, catalogue
        self.nonce = None
        self.sent_at = [float('-inf')]*3
        self.fingerprints = [None]*3
        self.generations = [0]*3
        self.last_poll = float('-inf')

    async def step(self, client, config, payload):
        tick = time.monotonic()
        if tick-self.last_poll<2: return
        self.last_poll = tick
        info = await self.read_info(client,config)
        if info.get('attention_v') != 2: return
        if info['nonce'] != self.nonce:
            self.nonce=info['nonce'];self.fingerprints=[None]*3;self.sent_at=[float('-inf')]*3
        live = isinstance(payload,dict) and payload.get('hub_alive') and time.time()-5<=payload.get('generated_at',0)<=time.time()+5
        for p,provider in enumerate(PROVIDERS):
            entries = self.catalogue(payload,provider,time.time())[0] if live else []
            rows=[]
            for e in entries:
                summary=e.get('summary',{})
                if summary.get('kind') not in ('progress','result') or not summary.get('text'):continue
                rows.append({'session_wire':e['wire_id'],'id':e['wire_id'], 'text':clean_question(summary['text']), 'status':'active'})
            # Prioritize active/focused summaries in the bounded eight-item text channel.
            priority={e['wire_id']:(not e.get('focused'),not e.get('active')) for e in entries}
            rows.sort(key=lambda r:priority[r['id']]);rows=rows[:8]
            fp=tuple((r['id'],r['text']) for r in rows)
            changed=fp!=self.fingerprints[p]
            if not changed and tick-self.sent_at[p]<8:continue
            if changed:self.generations[p]+=1
            gen=self.generations[p]
            packets=question_packets(gen,rows,len(rows)) if changed else [(3,struct.pack('<IIB',gen,0,0))]
            info=await self.read_info(client,config)
            for op,body in packets:
                head=struct.pack('<BBBI8s',7,p,op,info['accepted']+1,bytes.fromhex(info['nonce']))+body
                await asyncio.wait_for(client.write_gatt_char(WRITE,head+hmac.digest(bytes.fromhex(config['key']),head,'sha256'),response=True),8)
                info=dict(info,accepted=info['accepted']+1)
            self.fingerprints[p]=fp;self.sent_at[p]=time.monotonic()
