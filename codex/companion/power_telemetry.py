"""Authenticated power telemetry, independent of AI task freshness.

No charging controls. UTC is synchronized on the existing authenticated channel.
Sample timestamps and receiver timestamps stay separate. Logs survive disconnects
and are never interpreted as proof of a full charge/discharge cycle.
"""
import asyncio
import datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import struct
import time

POWER = '9e7a0004-6ed4-4b6b-bf37-43dc42838c20'
WRITE = '9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
ROOT = Path.home() / '.local/state/xueqiu/device'
FIELDS = ('sample', 'uptime_s', 'timestamp', 'millivolts', 'percent', 'power',
          'quality', 'valid', 'awake_s', 'sleep_s', 'battery_s', 'start_percent',
          'start_kind', 'asleep', 'animated_signal', 'sd_rows', 'sd_errors', 'sample_age_s')


def decode(raw, key, nonce):
    if len(raw) > 512:
        raise ValueError('Power payload exceeds ATT limit')
    envelope = json.loads(raw)
    data = envelope.get('data')
    if not isinstance(data, dict) or set(data) != {'v', 'n', 'b', 't'}:
        raise ValueError('Invalid power schema')
    signed = json.dumps(data, separators=(',', ':'), ensure_ascii=True).encode()
    if not hmac.compare_digest(hmac.digest(key, signed, 'sha256').hex(), str(envelope.get('h', ''))):
        raise ValueError('Power authentication failed')
    if data['v'] != 1 or data['n'] != nonce:
        raise ValueError('Power nonce or version mismatch')
    if not isinstance(data['b'], str) or len(data['b']) != 8 or any(c not in '0123456789abcdef' for c in data['b']):
        raise ValueError('Invalid power boot identity')
    values = data['t']
    if not isinstance(values, list) or len(values) != len(FIELDS) or any(type(x) is not int for x in values):
        raise ValueError('Invalid power fields')
    out = dict(zip(FIELDS, values), boot=data['b'], version=1)
    for name in ('valid', 'asleep', 'animated_signal'):
        if out[name] not in (0, 1): raise ValueError('Invalid boolean')
        out[name] = bool(out[name])
    if not -1 <= out['percent'] <= 100 or not -1 <= out['start_percent'] <= 100:
        raise ValueError('Invalid percentage')
    if out['power'] not in range(4) or out['quality'] not in range(3) or out['start_kind'] not in range(3):
        raise ValueError('Invalid power mode')
    for name in ('sample', 'uptime_s', 'timestamp', 'millivolts', 'awake_s', 'sleep_s', 'battery_s', 'sd_rows', 'sd_errors', 'sample_age_s'):
        if not 0 <= out[name] <= 0xffffffff: raise ValueError('Invalid unsigned field')
    if out['valid'] and not 2800 <= out['millivolts'] <= 5500:
        raise ValueError('Invalid voltage')
    if out['timestamp'] and not 1577836800 <= out['timestamp'] <= 4102444800:
        raise ValueError('Invalid device clock')
    out['timestamp'] = out['timestamp'] or None
    out['estimated'] = out['quality'] == 1
    out['valid'] = out['valid'] and out['sample_age_s'] <= 20
    return out


def clock_packet(key, info, epoch):
    if type(epoch) is not int or not 1577836800 <= epoch <= 4102444800:
        raise ValueError('Clock out of range')
    body = struct.pack('<BBBI8sI', 5, 0, 0, info['accepted'] + 1,
                       bytes.fromhex(info['nonce']), epoch)
    return body + hmac.digest(key, body, 'sha256')


def atomic(path, payload):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(payload, stream, ensure_ascii=False)
    os.replace(tmp, path)


class PowerRelay:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.last_read = self.last_clock = self.last_log = float('-inf')
        self.last_mode = self.last_boot = None
        self.failed = False

    async def step(self, client, config, read_info, source=None, target=None):
        now = time.monotonic()
        if now - self.last_read < 10:
            return
        self.last_read = now
        if client.services.get_characteristic(POWER) is None:
            return  # Older firmware stays fully compatible.
        try:
            info = await read_info(client, config)
            key = bytes.fromhex(config['key'])
            if now - self.last_clock >= 600 or info['nonce'] != self.last_boot:
                await asyncio.wait_for(client.write_gatt_char(WRITE, clock_packet(key, info, int(time.time())), response=True), 8)
                self.last_clock = now
                self.last_boot = info['nonce']
            raw = bytes(await asyncio.wait_for(client.read_gatt_char(POWER), 8))
            row = decode(raw, key, info['nonce'])
            received = time.time()
            row.update(received_at=received, received_iso=datetime.datetime.fromtimestamp(received, datetime.timezone.utc).isoformat(),
                       connected=True, device=config['device'], source='device',
                       provider=(source or {}).get('provider'),
                       task_fresh=(source or {}).get('task_fresh') is True,
                       status_current=target is not None and (target.valid_until is None or target.valid_until > received),
                       ai_status=(source or {}).get('status', 'unknown'))
            atomic(self.root / 'latest.json', row)
            identity = (row['boot'], row['power'], row['valid'])
            if identity != self.last_mode or now - self.last_log >= 60:
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                date = datetime.datetime.fromtimestamp(received, datetime.timezone.utc).strftime('%Y-%m-%d')
                fd = os.open(self.root / f'power-{date}.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, 'a') as stream:
                    stream.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
                self.last_log, self.last_mode = now, identity
            if self.failed:
                print(f'{row["received_iso"]} Power telemetry recovered', flush=True)
            self.failed = False
        except Exception as error:
            # A battery read failure must not replace or stop legitimate AI updates.
            if not self.failed:
                print(f'{datetime.datetime.now(datetime.timezone.utc).isoformat()} Power telemetry unavailable ({type(error).__name__})', flush=True)
            self.failed = True
