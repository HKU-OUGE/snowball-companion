#!/usr/bin/env python3
"""Snowball BLE bridge. Status, session names and posted-question titles cross BLE; no answers or execution commands."""
import argparse
import asyncio
import hashlib
import hmac
import json
import math
from collections import namedtuple
import os
from pathlib import Path
import struct
import sys
import time
import unicodedata
import http.client

SERVICE = '9e7a0001-6ed4-4b6b-bf37-43dc42838c20'
INFO = '9e7a0002-6ed4-4b6b-bf37-43dc42838c20'
WRITE = '9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
PROVIDERS = ['Codex', 'Claude', 'Cursor']
STATES = ['idle', 'thinking', 'running', 'waiting', 'completed', 'failed']
CONFIG = Path.home() / '.config/xueqiu/ble-pairing.json'

def transport_io_timeout(client, default=8):
    """A remote adapter may extend transport I/O; BLE keeps its existing limit."""
    value = getattr(client, 'io_timeout', default)
    return value if type(value) in (int, float) and math.isfinite(value) and 0 < value <= 24 else default



# BLE refresh is a transport lease, never evidence of a new AI task event.
HEARTBEAT_SECONDS = 5.0
FUTURE_SKEW_SECONDS = 5.0
SourceStatus = namedtuple('SourceStatus', 'provider state mode task_updated_at valid_until session_id display_name', defaults=[None, None, ''])


class SourceUnavailable(ValueError):
    """The source cannot currently justify a status write to the watch."""


ATTENTION_HUB_LEASE = 30


def pending_attention(payload, now, entry=None):
    """Durable unanswered items, leased by a live hub, not by AI activity.

    A stopped hub cannot keep an old pending snapshot alive indefinitely. The
    original source/task timestamps and freshness flags are never rewritten.
    """
    record = payload if entry is None else entry
    count = record.get('pending_input', 0)
    stamp = payload.get('generated_at')
    phase = record.get('state') if entry is None else record.get('phase')
    return (type(count) is int and 0 < count <= 4096 and phase == 'waiting' and
            payload.get('hub_alive') is True and
            type(stamp) in (int, float) and math.isfinite(stamp) and
            now - ATTENTION_HUB_LEASE < stamp <= now + FUTURE_SKEW_SECONDS)


def parse_source(payload, now=None):
    """Validate manual 2-key JSON or a version-1 status-hub snapshot.

    Manual JSON is deliberately an indefinite operator selection. Hub snapshots
    have a bounded source lease; timestamps/file mtime are never refreshed here.
    """
    now = time.time() if now is None else now
    if not isinstance(payload, dict):
        raise SourceUnavailable('source must be a JSON object')
    if payload.get('status') == 'ready':
        stamp = payload.get('generated_at')
        relevant = [r for r in payload.get('sessions', []) if payload.get('focus_provider') is None or r.get('provider') == payload['focus_provider']]
        if (payload.get('version') != 1 or payload.get('hub_alive') is not True or payload.get('state') != 'idle' or
                payload.get('provider') not in PROVIDERS or type(stamp) not in (int, float) or
                not now - ATTENTION_HUB_LEASE < stamp <= now + FUTURE_SKEW_SECONDS or
                any(r.get('pending_input', 0) or r.get('phase') not in ('idle', 'completed', None) for r in relevant)):
            raise SourceUnavailable('Unverified idle observation')
        return SourceStatus(payload['provider'], 'idle', 'hub', None, stamp + ATTENTION_HUB_LEASE, None, '')
    if set(payload) == {'provider', 'state'}:
        mode, task_updated_at, valid_until = 'manual', None, None
    else:
        if type(payload.get('version')) is not int or payload['version'] != 1:
            raise SourceUnavailable('unsupported snapshot version')
        attention = pending_attention(payload, now) and payload.get('status') == 'attention'
        quiet = (payload.get('status') == 'quiet_attention' and payload.get('state') == 'idle' and
                 type(payload.get('pending_total')) is int and payload['pending_total'] > 0 and
                 type(payload.get('generated_at')) in (int, float) and now - 30 < payload['generated_at'] <= now + 5)
        attention = attention or quiet
        if not attention and (payload.get('source_fresh') is not True or payload.get('task_fresh') is not True
                or payload.get('status') != 'known'):
            raise SourceUnavailable('AI source is stale or unknown')
        if payload.get('hub_alive') is not True:
            raise SourceUnavailable('status hub is unavailable')
        timestamps = {}
        for name in ('generated_at', 'updated_at', 'expires_at', 'task_updated_at', 'task_expires_at'):
            value = payload.get(name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise SourceUnavailable('snapshot timestamp is missing or invalid')
            timestamps[name] = value
        ttl = payload.get('ttl_seconds')
        if type(ttl) is not int or not 5 <= ttl <= 300:
            raise SourceUnavailable('snapshot source TTL is invalid')
        if not attention and min(timestamps['expires_at'], timestamps['task_expires_at']) <= now:
            raise SourceUnavailable('AI source lease has expired')
        for name in ('generated_at', 'updated_at', 'task_updated_at'):
            if timestamps[name] > now + FUTURE_SKEW_SECONDS:
                raise SourceUnavailable('snapshot timestamp is in the future')
        if (timestamps['updated_at'] > timestamps['generated_at'] + FUTURE_SKEW_SECONDS
                or timestamps['task_updated_at'] > timestamps['updated_at'] + FUTURE_SKEW_SECONDS
                or timestamps['expires_at'] > timestamps['updated_at'] + ttl + .001
                or timestamps['task_expires_at'] > timestamps['task_updated_at'] + 300.001):
            raise SourceUnavailable('snapshot timestamp ordering is invalid')
        mode, task_updated_at = 'hub', timestamps['task_updated_at']
        valid_until = (timestamps['generated_at'] + ATTENTION_HUB_LEASE if attention else
                       min(timestamps['expires_at'], timestamps['task_expires_at']))
    provider, state = payload.get('provider'), payload.get('state')
    if provider not in PROVIDERS or state not in STATES:
        raise SourceUnavailable('unsupported provider/state')
    session_id, title = None, ''
    if mode == 'hub' and payload.get('session_id') is not None:
        raw_id = payload['session_id']
        if not isinstance(raw_id, str) or not 1 <= len(raw_id) <= 96:
            raise SourceUnavailable('invalid session metadata')
        # Namespace all identities, including upstream opaque 128-bit IDs.
        session_id = wire_id(payload)
        title = payload.get('display_name', '')
        try:
            validate_title(title)
        except ValueError:
            raise SourceUnavailable('invalid display name') from None
    elif payload.get('display_name'):
        raise SourceUnavailable('display name has no session identity')
    return SourceStatus(provider, state, mode, task_updated_at, valid_until, session_id, title)


class StatusRelay:
    """Keep the BLE link observable without extending an expired task lease."""
    def __init__(self):
        self.previous = None
        self.last_io = float('-inf')

    async def step(self, client, config, target, now=None):
        now = time.monotonic() if now is None else now
        if target is None:
            self.previous = None
            if now - self.last_io >= HEARTBEAT_SECONDS:
                # Read-only proof checks do not touch firmware last_rx or its TTL.
                await read_info(client, config)
                self.last_io = now
            return None
        state = target.provider, target.state
        identity = (*state, target.session_id, target.display_name)
        if identity != self.previous or now - self.last_io >= HEARTBEAT_SECONDS:
            kwargs = {'valid_until': target.valid_until}
            if target.session_id is not None:
                kwargs.update(session_id=target.session_id, display_name=target.display_name)
            ack = await send(client, config, *state, **kwargs)
            self.previous, self.last_io = identity if ack is not None else None, now
            return ack
        return None


def validate_title(title):
    if (not isinstance(title, str) or len(title) > 64
            or any(unicodedata.category(c)[0] == 'C' for c in title)):
        raise ValueError('Invalid display name')
    if len(title.encode('utf-8')) > 192:
        raise ValueError('Display name exceeds UTF-8 budget')


def make_packet(key, info, provider, state, seq=None, session_id=None, display_name=''):
    p = PROVIDERS.index(provider)
    s = STATES.index(state)
    seq = info['accepted'] + 1 if seq is None else seq
    head = struct.pack('<BBBI8s', 1, p, s, seq, bytes.fromhex(info['nonce']))
    if session_id is not None:
        if len(session_id) != 32 or any(c not in '0123456789abcdef' for c in session_id):
            raise ValueError('Session ID must be 128-bit lowercase hex')
        validate_title(display_name)
        title = display_name.encode('utf-8')
        head = bytes([2]) + head[1:] + bytes.fromhex(session_id) + bytes([len(title)]) + title
    elif display_name:
        raise ValueError('Display name requires session ID')
    return head + hmac.digest(key, head, 'sha256')


def wire_id(entry):
    # Always namespace, even when upstream IDs already look opaque.
    return hashlib.sha256((str(entry.get('source', '')) + '/' + entry['session_id']).encode()).hexdigest()[:32]


def make_catalog_packet(key, info, provider, opcode, body):
    head = struct.pack('<BBBI8s', 3, PROVIDERS.index(provider), opcode,
                       info['accepted'] + 1, bytes.fromhex(info['nonce'])) + body
    if len(head) + 32 > 272:
        raise ValueError('catalog packet too large')
    return head + hmac.digest(key, head, 'sha256')


def decode_order(encoded):
    try:
        raw = bytes.fromhex(encoded)
    except (ValueError, TypeError):
        raise ValueError('invalid order encoding') from None
    if len(raw)==49:
        count=raw[0]
        if not 1<=count<=64:raise ValueError('invalid order length')
        bits=int.from_bytes(raw[1:],'little');order=[(bits>>(6*i))&63 for i in range(count)]
        if sorted(order)!=list(range(count)) or bits>>(6*count):raise ValueError('invalid order permutation')
        return order
    if len(raw) != 16 or not 1 <= raw[0] <= 8:
        raise ValueError('invalid order length')
    count = raw[0]
    order = list(raw[1:count + 1])
    if sorted(order) != list(range(count)) or any(raw[count + 1:]):
        raise ValueError('invalid order permutation')
    return order


def verify_request(info, config):
    request = info.get('request')
    if info.get('catalog_v') not in (1, 2, 3) or not isinstance(request, dict):
        return None
    rid = request.get('id', '')
    if rid and (len(rid) != 32 or any(c not in '0123456789abcdef' for c in rid)):
        raise ValueError('invalid focus identity')
    if type(request.get('pending')) is not bool or type(request.get('provider')) is not int or not 0 <= request['provider'] < 3:
        raise ValueError('invalid focus request')
    raw = struct.pack('<8sIBIB16s', bytes.fromhex(info['nonce']), request['seq'],
                      request['provider'], request['generation'], request['pending'],
                      bytes.fromhex(rid) if rid else bytes(16))
    if info.get('catalog_v') in (2,3):
        action, before = request.get('action'), request.get('before')
        if type(action) is not int or action not in range(13 if info.get('catalog_v')==3 else 12) or not isinstance(before, str) or (before and (len(before) != (98 if action==12 else 32) or any(c not in '0123456789abcdef' for c in before))):
            raise ValueError('invalid priority request')
        if 3<=action<=11 and (not rid or not before):
            raise ValueError('missing question identity')
        if action == 1 and not rid:
            raise ValueError('missing moved session')
        if action == 0 and before:
            raise ValueError('unexpected priority target')
        if action in (2,12):
            if rid:
                raise ValueError('batch order cannot select focus identity')
            decode_order(before)
        raw += bytes([action]) + (bytes.fromhex(before) if before else bytes(16))
    elif request.get('action', 0) != 0 or request.get('before'):
        raise ValueError('priority move requires catalogue v2')
    proof = hmac.digest(bytes.fromhex(config['key']), raw, 'sha256').hex()
    if not hmac.compare_digest(proof, request.get('proof', '')):
        raise ValueError('focus request authentication failed')
    return request


def focus_http(request, token_path=None):
    token_path = token_path or Path.home() / '.config/xueqiu/hub-token'
    connection = http.client.HTTPConnection('127.0.0.1', 4190, timeout=2)
    try:
        connection.request('POST', '/v1/focus', json.dumps(request),
                           {'Authorization': 'Bearer ' + token_path.read_text().strip(), 'Content-Type': 'application/json'})
        response = connection.getresponse()
        body = response.read(262144)
        if response.status != 200:
            raise ValueError('hub focus rejected')
        return json.loads(body)
    finally:
        connection.close()


class CatalogRelay:
    """Atomic leased catalogues; reverse selection is authenticated and identity-bound."""
    def __init__(self):
        self.limit=8
        self.entries = {}
        self.sent_catalogs = {}
        self.generations = [0, 0, 0]
        self.last_sent_gen=[None]*3
        self.fingerprints = [None] * 3
        self.sent_at = [float('-inf')] * 3
        self.last_poll = float('-inf')
        self.handled = None
        self.results = {}
        self.nonce = None

    def catalogue(self, payload, provider, now):
        if not isinstance(payload, dict) or payload.get('hub_alive') is not True or payload.get('version') != 1:
            return [], 0, False, ''
        focused = payload.get('focused_session')
        directory_fresh=isinstance(payload.get('generated_at'),(int,float)) and now-5<=payload['generated_at']<=now+5
        rows = []
        for entry in payload.get('sessions', []):
            if entry.get('provider') != provider or entry.get('phase') not in STATES:
                continue
            try:
                validate_title(entry.get('display_name', ''))
                expiry = min(entry['expires_at'], entry['task_expires_at'] or 0)
                if not math.isfinite(expiry) or not isinstance(entry.get('source'), str) or not isinstance(entry.get('session_id'), str):
                    continue
                attention = pending_attention(payload, now, entry)
                quiet_questions = entry.get('pending_total', 0) > 0 and not attention
                if attention:
                    expiry = payload['generated_at'] + ATTENTION_HUB_LEASE
                active = attention or (entry['phase'] in ('thinking', 'running', 'waiting', 'failed') and
                          entry.get('source_fresh') is True and entry.get('task_fresh') is True and expiry > now)
                key = entry['source'] + '/' + entry['session_id']
                if not ((directory_fresh and entry.get('sidebar_visible') is True) or attention or quiet_questions or (entry.get('source_fresh') is True and entry.get('task_fresh') is True and expiry > now) or (payload.get('focus_pinned') and key == focused)):
                    continue
                known=attention or quiet_questions or (entry.get('source_fresh') is True and entry.get('task_fresh') is True and expiry>now)
                phase=entry['phase']
                if entry.get('sidebar_visible') and not known and phase in ('idle','completed'):
                    phase='idle';known=True
                rows.append(dict(entry, phase=phase, known=known, display_name=entry.get('display_name', ''), wire_id=wire_id(entry), active=active, expiry=expiry, focused=key == focused))
            except (KeyError, TypeError, ValueError):
                continue
        total = sum(row['active'] for row in rows)
        # Keep the wire list in user priority order; reserve scarce slots for
        # actionable rows before terminal history. A fresh urgent focus is never lost.
        rows.sort(key=lambda row: row.get('priority', 0))
        chosen = ([r for r in rows if r['active']] + [r for r in rows if not r['active']])[:self.limit]
        focus_row = next((r for r in rows if r['focused']), None)
        if focus_row and focus_row['active'] and focus_row not in chosen:
            chosen = chosen[:self.limit-1] + [focus_row]
        chosen.sort(key=lambda row: row.get('priority', 0))
        pinned = bool(payload.get('focus_pinned'))
        # Reserve a slot for pinned terminal focus; it must remain identifiable.
        focus = next((r for r in rows if r['focused']), None)
        if pinned and focus and focus not in chosen:
            chosen = chosen[:self.limit-1] + [focus]
        # An expired auto-focus cannot produce a v2 state/title update. Send the
        # empty sentinel so watch confirmation does not await an impossible ID.
        # Fresh completed/failed focus remains visible while its lease is valid.
        display_focus = focus is not None and (pinned or (focus.get('pending_total', 0) > 0 and focus['phase'] == 'idle') or pending_attention(payload, now, focus) or (focus['expiry'] > now and focus.get('source_fresh') is True and focus.get('task_fresh') is True))
        return chosen, total, pinned and focus is not None, focus['wire_id'] if display_focus else ''

    async def step(self, client, config, payload):
        tick = time.monotonic()
        if tick - self.last_poll < 1.0:
            return None
        self.last_poll = tick
        info = await read_info(client, config)
        if info.get('catalog_v') not in (1, 2, 3):
            return None
        self.limit=64 if info.get('catalog_v')==3 else 8
        if info['nonce'] != self.nonce:
            self.nonce = info['nonce']
            self.sent_at = [float('-inf')] * 3
            self.entries.clear()
            self.sent_catalogs.clear()
            self.last_sent_gen=[None]*3
            self.handled = None
        request = verify_request(info, config)
        refreshed = None
        if request and request['pending'] and (request.get('action', 0) < 3 or request.get('action')==12) and (info['nonce'], request['seq']) != self.handled:
            self.handled = (info['nonce'], request['seq'])
            p = request['provider']
            result = 2
            try:
                sent = self.sent_catalogs.get((p, request['generation']))
                if not sent or tick - sent['sent_at'] >= 20:
                    raise ValueError('expired catalogue request')
                if request.get('action', 0) in (2,12):
                    permutation = decode_order(request['before'])
                    original = list(sent['entries'].values())
                    current_rows, *_ = self.catalogue(payload, PROVIDERS[p], time.time())
                    if (len(original) != len(permutation) or
                            {r['wire_id'] for r in original} != {r['wire_id'] for r in current_rows}):
                        raise ValueError('batch catalogue membership changed')
                    choice = {'provider': PROVIDERS[p], 'order': [
                        original[i]['source'] + '/' + original[i]['session_id'] for i in permutation]}
                elif request.get('action', 0) == 1:
                    current_rows, *_ = self.catalogue(payload, PROVIDERS[p], time.time())
                    current_ids = {r['wire_id'] for r in current_rows}
                    def resolve(identity):
                        row = sent['entries'].get(identity)
                        if not row or identity not in current_ids:
                            raise ValueError('priority row expired or replaced')
                        return row['source'] + '/' + row['session_id']
                    choice = {'provider': PROVIDERS[p], 'move': resolve(request['id']),
                              'before': resolve(request['before']) if request['before'] else None}
                elif request['id']:
                    row = sent['entries'].get(request['id'])
                    if not row or not row['active'] or row['expiry'] <= time.time():
                        raise ValueError('expired session selection')
                    # A click can race a new catalogue commit. Authorize against
                    # the actually displayed generation, then recheck current
                    # evidence independently (also when outside today's top 8).
                    current_entries = [r for r in (payload or {}).get('sessions', [])
                                       if r.get('source') == row['source'] and r.get('session_id') == row['session_id']]
                    current_rows, *_ = self.catalogue(dict(payload or {}, sessions=current_entries), PROVIDERS[p], time.time())
                    if len(current_rows) != 1 or not current_rows[0]['active'] or current_rows[0]['wire_id'] != request['id']:
                        raise ValueError('selected session is no longer active')
                    choice = {'source': row['source'], 'session_id': row['session_id']}
                else:
                    choice = {'auto': True, 'provider': PROVIDERS[p]}
                # Re-read authenticated request immediately before changing hub focus.
                current = verify_request(await read_info(client, config), config)
                if not current or not current['pending'] or current['seq'] != request['seq']:
                    raise ValueError('request expired or replaced')
                refreshed = await asyncio.to_thread(focus_http, choice)
                if choice.get('auto') or 'move' in choice or 'order' in choice:
                    if refreshed.get('focus_pinned') or refreshed.get('focus_provider') != PROVIDERS[p]:
                        raise ValueError('hub did not confirm provider auto focus')
                    if 'order' in choice:
                        actual = [k for k in refreshed.get('priority_order', []) if k in choice['order']]
                        if actual != choice['order']:
                            raise ValueError('hub did not confirm batch order')
                elif not refreshed.get('focus_pinned') or refreshed.get('focused_session') != choice['source'] + '/' + choice['session_id']:
                    raise ValueError('hub did not confirm selected focus')
                result = 1
            except (OSError, ValueError, TimeoutError, http.client.HTTPException):
                pass
            self.results[p] = (request['seq'], result)
            self.sent_at[p] = float('-inf')
            if refreshed is not None:
                payload = refreshed
        for p, provider in enumerate(PROVIDERS):
            rows, total, pinned, focused = self.catalogue(payload, provider, time.time())
            fingerprint = (tuple((r['wire_id'], r['phase'], r['display_name'], r['active'],r.get('known',True)) for r in rows), total, pinned, focused,self.results.get(p,(0,0)),bool((payload or {}).get('can_create') and p==0))
            unchanged=fingerprint==self.fingerprints[p]
            if fingerprint != self.fingerprints[p]:
                self.generations[p] += 1
                self.fingerprints[p] = fingerprint
            elif tick - self.sent_at[p] < 8:
                continue
            gen = self.generations[p]
            ack, result = self.results.get(p, (0, 0))
            body = struct.pack('<IBHB16sIB', gen, len(rows), total, int(pinned) | result << 1 | (8 if info.get('catalog_v')==3 and p==0 and (payload or {}).get('can_create') else 0),
                               bytes.fromhex(focused) if focused else bytes(16), ack, 20)
            packets = [(0, body)]
            for index, row in enumerate(rows):
                title = row.get('display_name', '').encode()
                lease = max(0, min(20, int(row['expiry'] - time.time()))) if row['active'] else 0
                packets.append((1, struct.pack('<IB16sBBB', gen, index, bytes.fromhex(row['wire_id']), STATES.index(row['phase']) | (128 if info.get('catalog_v')==3 and not row.get('known',True) else 0), lease, len(title)) + title))
            packets.append((2, struct.pack('<I', gen)))
            previous=self.sent_catalogs.get((p,self.last_sent_gen[p]))
            if info.get('catalog_v')==3 and unchanged and (p,gen) in self.sent_catalogs:
                leases=bytes(max(0,min(20,int(row['expiry']-time.time()))) if row['active'] else 0 for row in rows)
                packets=[(3,struct.pack('<IB',gen,len(rows))+leases)]
            elif info.get('catalog_v')==3 and previous and [(r['wire_id'],r['display_name']) for r in previous['entries'].values()]==[(r['wire_id'],r['display_name']) for r in rows]:
                flags=int(pinned)|result<<1|(8 if p==0 and (payload or {}).get('can_create') else 0)
                delta=struct.pack('<IIBHB16sIB',self.last_sent_gen[p],gen,len(rows),total,flags,bytes.fromhex(focused) if focused else bytes(16),ack,20)
                for row in rows:
                    delta+=bytes([STATES.index(row['phase'])|(0 if row.get('known',True) else 128),max(0,min(20,int(row['expiry']-time.time()))) if row['active'] else 0])
                packets=[(4,delta)]
            info = await read_info(client, config)
            for op, body in packets:
                # ATT response confirms acceptance; serialize and locally advance sequence.
                if op == 1:
                    row = rows[body[4]]
                    lease = max(0, min(20, int(row['expiry'] - time.time()))) if row['active'] else 0
                    body = body[:22] + bytes([lease]) + body[23:]
                packet = make_catalog_packet(bytes.fromhex(config['key']), info, provider, op, body)
                await asyncio.wait_for(client.write_gatt_char(WRITE, packet, response=True), transport_io_timeout(client))
                info = dict(info, accepted=info['accepted'] + 1)
            self.sent_at[p] = time.monotonic()
            self.entries = {k: v for k, v in self.entries.items() if k[0] != p}
            self.entries.update({(p, row['wire_id']): row for row in rows})
            self.last_sent_gen[p]=gen
            self.sent_catalogs[(p, gen)] = {'sent_at': self.sent_at[p], 'entries': {row['wire_id']: dict(row) for row in rows}}
            self.sent_catalogs = {k: v for k, v in self.sent_catalogs.items() if self.sent_at[p] - v['sent_at'] < 20}
            # At most one changed generation/second/provider; cap independently
            # as defense in depth while preserving every unexpired display.
            for provider_id in range(3):
                keys = sorted((k for k in self.sent_catalogs if k[0] == provider_id), key=lambda k: self.sent_catalogs[k]['sent_at'], reverse=True)
                for old in keys[32:]:
                    del self.sent_catalogs[old]
        return refreshed


def verify_info(info, config):
    if info.get('v') != 1 or info.get('device') != config['device']:
        raise ValueError('Device identity/version mismatch')
    msg = b'XQINFO' + struct.pack('<8sIIBB', bytes.fromhex(info['nonce']), info['accepted'], info['applied'], info['provider'], info['state'])
    expected = hmac.digest(bytes.fromhex(config['key']), msg, 'sha256').hex()
    if not hmac.compare_digest(expected, info.get('proof', '')):
        raise ValueError('Device authentication failed; USB-bind the intended watch first')
    return info


def pair_usb(port, config_path):
    import serial
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, .2
    s.dtr = s.rts = False
    s.open()
    try:
        # This board may reset on opening USB. Never print the pairing response.
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            s.read(8192)
        s.write(b':PAIR\n')
        s.flush()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            raw = s.readline()
            idx = raw.find(b'XQPAIR ')
            if idx < 0:
                continue
            config = json.loads(raw[idx+7:])
            if config.get('v') != 1 or len(bytes.fromhex(config['key'])) != 32:
                raise ValueError('Invalid pairing response')
            config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(config_path.parent, 0o700)
            temporary = config_path.with_suffix('.tmp')
            fd = os.open(temporary, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
            with os.fdopen(fd, 'w') as f:
                json.dump(config, f)
                f.write('\n')
            os.chmod(temporary, 0o600)
            temporary.replace(config_path)
            print(f'USB identity bound: {config["device"]}; private pairing file saved.', flush=True)
            return
        raise TimeoutError('No pairing response. Check firmware and USB port.')
    finally:
        s.close()


def cached_peer(objects,config):
    """An already-connected/bonded peer need not be broadcasting its name."""
    candidates=[]
    for path,interfaces in objects.items():
        values=interfaces.get('org.bluez.Device1')
        if not values:continue
        props={k:getattr(v,'value',v) for k,v in values.items()}
        if props.get('Name')!=config['device'] or not (props.get('Connected') or props.get('Paired') or props.get('Bonded')):continue
        if not isinstance(props.get('Address'),str):continue
        candidates.append((path,props))
    if len(candidates)>1:raise ValueError('Multiple cached Snowball peers; explicit rebinding required')
    return candidates[0] if candidates else None


async def cached_linux_device(config):
    if sys.platform!='linux':return None
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType,Message,MessageType
    from bleak.backends.device import BLEDevice
    bus=await MessageBus(bus_type=BusType.SYSTEM).connect()
    try:
        response=await asyncio.wait_for(bus.call(Message(destination='org.bluez',path='/',interface='org.freedesktop.DBus.ObjectManager',member='GetManagedObjects')),3)
        if response.message_type==MessageType.ERROR:return None
        peer=cached_peer(response.body[0],config)
        if peer:
            path,props=peer
            return BLEDevice(props['Address'],props.get('Name'),{'path':path,'props':props})
    finally:bus.disconnect()


async def find_device(config=None):
    if config:
        device=await cached_linux_device(config)
        if device is not None:return device
    from bleak import BleakScanner
    found = await BleakScanner.discover(timeout=8, return_adv=True)
    candidates = []
    for device, adv in found.values():
        if SERVICE not in [u.lower() for u in adv.service_uuids]:
            continue
        name = adv.local_name or device.name or ''
        if config is None:
            print(f'Found {name}', flush=True)
        elif name == config['device']:
            candidates.append(device)
    if config is None:
        return None
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one {config["device"]}, found {len(candidates)}. Ensure the watch is on and not connected elsewhere.')
    return candidates[0]


async def linux_agent(device):
    """Register an agent on the SAME D-Bus connection used for Pair, scoped to this device."""
    # Bleak owns a separate D-Bus connection. Its Pair call is handled by a default
    # agent if installed. We explicitly call Pair using our own bus below instead.
    if sys.platform != 'linux':
        return None
    from dbus_fast.aio import MessageBus
    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.service import ServiceInterface, method
    from dbus_fast.errors import DBusError
    target = device.details['path']

    class Agent(ServiceInterface):
        def __init__(self):
            super().__init__('org.bluez.Agent1')

        def check(self, path):
            if path != target:
                raise DBusError('org.bluez.Error.Rejected', 'Not the bound Snowball')

        @method()
        def Release(self):
            pass

        @method()
        def RequestAuthorization(self, device: 'o'):
            self.check(device)

        @method()
        def AuthorizeService(self, device: 'o', uuid: 's'):
            self.check(device)

        @method()
        def RequestConfirmation(self, device: 'o', passkey: 'u'):
            self.check(device)
            raise DBusError('org.bluez.Error.Rejected', 'Numeric comparison requires user verification')

        @method()
        def Cancel(self):
            pass

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
    path = f'/org/xueqiu/agent{os.getpid()}'
    bus.export(path, Agent())
    response = await bus.call(Message(destination='org.bluez', path='/org/bluez', interface='org.bluez.AgentManager1', member='RegisterAgent', signature='os', body=[path, 'NoInputNoOutput']))
    if response.message_type == MessageType.ERROR:
        bus.disconnect()
        raise RuntimeError(f'Bluetooth agent registration failed: {response.error_name}')
    paired = await bus.call(Message(destination='org.bluez', path=target, interface='org.freedesktop.DBus.Properties', member='Get', signature='ss', body=['org.bluez.Device1', 'Paired']))
    if paired.message_type != MessageType.ERROR and paired.body[0].value:
        return bus
    response = await asyncio.wait_for(bus.call(Message(destination='org.bluez', path=target, interface='org.bluez.Device1', member='Pair')), 25)
    if response.message_type == MessageType.ERROR and response.error_name != 'org.bluez.Error.AlreadyExists':
        bus.disconnect()
        raise RuntimeError(f'Bluetooth pairing failed: {response.error_name}')
    return bus


class InfoTransportError(OSError):
    """Incomplete or invalid GATT payload: reconnect without exposing its contents."""


def decode_info(raw):
    try:
        info = json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise InfoTransportError('Incomplete or malformed Bluetooth INFO') from error
    if not isinstance(info, dict):
        raise InfoTransportError('Invalid Bluetooth INFO object')
    if info.get('v') not in (2, 3, 4, 5, 6):
        return info  # Existing v1 firmware remains supported.
    try:
        applied, provider, state = info['s']
        rq, rp, rg, pending, rid, action, before, request_proof = info['r']
        ps, pr, pet_id, pet_proof = info['p']
        return {'v': 1, 'device': info['d'], 'nonce': info['n'],
                'accepted': info['a'], 'applied': applied, 'provider': provider,
                'state': state, 'proof': info['h'], 'catalog_v': 3 if info['v']>=5 else 2, 'network_v':1 if info['v']>=6 else 0, 'attention_v': 2 if info['v'] >= 4 else 1 if info['v'] == 3 else 0,
                'request': {'seq': rq, 'provider': rp, 'generation': rg,
                            'pending': pending, 'id': rid, 'action': action,
                            'before': before, 'proof': request_proof},
                'pet_v': 1, 'pet_seq': ps, 'pet_result': pr,
                'pet_id': pet_id, 'pet_proof': pet_proof}
    except (KeyError, TypeError, ValueError) as error:
        raise InfoTransportError('Invalid compact Bluetooth INFO schema') from error


async def read_info(client, config):
    raw = bytes(await asyncio.wait_for(client.read_gatt_char(INFO), transport_io_timeout(client)))
    return verify_info(decode_info(raw), config)


async def send(client, config, provider, state, valid_until=None, session_id=None, display_name=''):
    info = await read_info(client, config)
    # Authentication reads can block; recheck the lease immediately before write.
    if valid_until is not None and time.time() >= valid_until:
        return None
    packet = make_packet(bytes.fromhex(config['key']), info, provider, state, session_id=session_id, display_name=display_name)
    seq = info['accepted'] + 1
    await asyncio.wait_for(client.write_gatt_char(WRITE, packet, response=True), transport_io_timeout(client))
    for _ in range(20):
        ack = await read_info(client, config)
        if ack['applied'] == seq and ack['provider'] == PROVIDERS.index(provider) and ack['state'] == STATES.index(state):
            return ack
        await asyncio.sleep(.05)
    raise TimeoutError('Command not acknowledged by watch UI')


async def connected_session(args, config):
    from bleak import BleakClient
    device = await find_device(config)
    agent = await linux_agent(device)
    speech = None
    try:
        async with BleakClient(device, timeout=20, pair=sys.platform != 'linux') as client:
            await read_info(client, config)
            print(f'Connected and authenticated: {config["device"]}', flush=True)
            if args.command == 'demo':
                for i, state in enumerate(STATES):
                    provider = PROVIDERS[i % 3]
                    ack = await send(client, config, provider, state)
                    print(f'Applied {provider}: {state}, seq={ack["applied"]}', flush=True)
                    await asyncio.sleep(3)
                return
            if args.command == 'check':
                ack = await read_info(client, config)
                print(json.dumps({k: ack[k] for k in ['v', 'device', 'accepted', 'applied', 'provider', 'state']}), flush=True)
                return
            started = time.monotonic()
            relay = StatusRelay()
            from power_telemetry import PowerRelay
            power = PowerRelay()
            catalogue = CatalogRelay()
            from attention_relay import AttentionRelay, SummaryRelay
            attention = AttentionRelay(read_info, verify_request)
            summaries = SummaryRelay(read_info, catalogue.catalogue)
            from speech_relay import SpeechRelay
            speech = SpeechRelay(read_info)
            from pet_sync import PetRelay
            # Preserve attempted revisions across reconnects with the same nonce.
            if not hasattr(args, '_pet_relay'):
                args._pet_relay = PetRelay()
            pets = args._pet_relay
            from network_provision import NetworkProvision
            network = NetworkProvision()
            source_notice = None
            previous = None
            last_bulk = float('-inf')
            while (args.command in ('watch', 'control') or time.monotonic() - started < max(args.hold, 1)) and not getattr(args, 'quit', False):
                # Keep secure provisioning available while Wi-Fi owns snapshots.
                if not speech.latency_sensitive and await network.step(client, config, read_info):
                    await asyncio.sleep(.5)
                    continue
                payload = None
                target = SourceStatus(args.provider, args.state, 'manual', None)
                notice = 'Manual/demo status source; no AI application is being observed'
                # During a short voice upload, keep live task/attention checks
                # responsive but renew bulky catalogs/summaries only every 8s.
                # Their firmware leases are 20s. All writes remain sequential.
                bulk_due = not speech.latency_sensitive or time.monotonic()-last_bulk >= 8
                if bulk_due:last_bulk=time.monotonic()
                if args.command == 'watch' and args.source:
                    try:
                        payload = json.loads(args.source.read_text())
                        target = parse_source(payload)
                        notice = ('Live status-hub source; BLE refreshes do not change task timestamps'
                                  if target.mode == 'hub' else notice)
                    except (OSError, ValueError) as error:
                        target = None
                        reason = str(error) if isinstance(error, SourceUnavailable) else 'source file unreadable or invalid JSON'
                        notice = f'Waiting for source: {reason}; BLE link probe only'
                if notice != source_notice:
                    print(notice, flush=True)
                    source_notice = notice
                refreshed_attention = None
                if bulk_due and args.command == 'watch' and args.source:
                    refreshed = await catalogue.step(client, config, payload)
                    if refreshed is not None:
                        # A focus ACK is not a UI snapshot. Send one even when
                        # identity/state matches the previous bridge target.
                        relay.previous = None
                        try:
                            target = parse_source(refreshed)
                        except SourceUnavailable:
                            target = None
                if args.command == 'watch' and args.source:
                    refreshed_attention = await attention.step(client, config, payload)
                if refreshed_attention is not None:
                    payload = refreshed_attention
                    try:
                        target = parse_source(payload)
                    except SourceUnavailable:
                        target = None
                if bulk_due:
                    await summaries.step(client, config, payload)
                    await pets.step(client, config, payload, read_info)
                ack = await relay.step(client, config, target)
                if target is None:
                    previous = None
                elif ack is not None and (target.provider, target.state) != previous:
                    print(f'Applied {target.provider}: {target.state}, seq={ack["applied"]}', flush=True)
                    previous = target.provider, target.state
                await power.step(client, config, read_info, payload, target)
                try:
                    await speech.step(client, config, payload)
                except Exception as error:
                    print(f'Speech transport failed: {type(error).__name__}; ATT={getattr(error,"code",None)}',flush=True)
                    raise
                await asyncio.sleep(.03 if speech.transferring else .2)
    finally:
        if speech is not None:
            await speech.close()
        if agent:
            agent.disconnect()


async def run(args):
    if args.command == 'scan':
        await find_device()
        return
    config = json.loads(args.config.read_text())
    delay = 2
    if args.command == 'control':
        async def console():
            print('\n雪球蓝牙控制：1 待机 / 2 处理中 / 3 执行中 / 4 等待确认 / 5 完成 / 6 异常；p 切软件；q 退出。', flush=True)
            print('这里推送的是手动状态，尚未读取 AI 软件的真实任务。', flush=True)
            while True:
                line = await asyncio.to_thread(sys.stdin.readline)
                command = line.strip().lower()
                if not line or command == 'q':
                    args.quit = True
                    return
                if command in ['1', '2', '3', '4', '5', '6']:
                    args.state = STATES[int(command)-1]
                elif command == 'p':
                    args.provider = PROVIDERS[(PROVIDERS.index(args.provider)+1) % 3]
                else:
                    print('请输入 1–6、p 或 q，然后回车。', flush=True)
        args.quit = False
        asyncio.create_task(console())
    while not getattr(args, 'quit', False):
        try:
            await connected_session(args, config)
            return
        except (ValueError, KeyError):
            raise  # Authentication/schema failures require intervention.
        except Exception as e:
            if args.command not in ('watch', 'control'):
                raise
            print(f'Link unavailable ({type(e).__name__}, ATT={getattr(e,"code",None)}, DBus={getattr(e,"dbus_error",None)}); reconnecting in {delay}s', flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 20)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--config', type=Path, default=CONFIG)
    sub = p.add_subparsers(dest='command', required=True)
    pair = sub.add_parser('pair-usb')
    pair.add_argument('--port', default='/dev/ttyACM0')
    for name in ['scan', 'check', 'demo']:
        sub.add_parser(name)
    for name in ['send', 'watch', 'control']:
        c = sub.add_parser(name)
        c.add_argument('--provider', choices=PROVIDERS, default='Codex')
        c.add_argument('--state', choices=STATES, default='idle')
        if name == 'send':
            c.add_argument('--hold', type=float, default=30)
        elif name == 'watch':
            c.add_argument('--source', type=Path, help='Read a manual 2-key JSON or an expiring version-1 status-hub snapshot')
    args = p.parse_args()
    if args.command == 'send' and args.hold <= 0:
        p.error('--hold must be positive')
    if args.command == 'control':
        import manage
        from user_services import owned,control
        if owned('bridge'):control('bridge','stop')
        else:manage.stop(quiet=True)
    if args.command == 'pair-usb':
        pair_usb(args.port, args.config)
    else:
        asyncio.run(run(args))


if __name__ == '__main__':
    import signal

    def stop_signal(_signum, _frame):
        # Let asyncio cancel tasks and leave the BleakClient context, releasing
        # BlueZ's connection before a managed restart starts scanning again.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    try:
        main()
    except KeyboardInterrupt:
        pass
