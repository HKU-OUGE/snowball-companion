"""Authenticated one-shot Pet selection relay; SD loading never blocks status polling."""
import asyncio
from collections import OrderedDict
import hmac
import math
import re
import struct
import time

WRITE = '9e7a0003-6ed4-4b6b-bf37-43dc42838c20'
CHARACTER_ID = re.compile(r'[a-z][a-z0-9-]{0,30}\Z')
IO_TIMEOUT = 1.0
POLL_SECONDS = 0.5
RESULT_TIMEOUT = 30.0


def valid_id(value):
    return isinstance(value, str) and CHARACTER_ID.fullmatch(value) is not None


def make_pet_packet(key, info, character_id):
    if not valid_id(character_id):
        raise ValueError('Invalid Pet character ID')
    sequence = info['accepted'] + 1
    if type(sequence) is not int or not 1 <= sequence <= 0xffffffff:
        raise ValueError('Pet sequence exhausted')
    nonce = bytes.fromhex(info['nonce'])
    if len(nonce) != 8:
        raise ValueError('Invalid Pet nonce')
    encoded = character_id.encode('ascii')
    head = struct.pack('<BBBI8s', 4, 0, 0, sequence, nonce) + bytes([len(encoded)]) + encoded
    return head + hmac.digest(key, head, 'sha256')


def verify_pet_info(info, key):
    """Verify the separate Pet proof after the caller verifies general INFO proof."""
    if info.get('pet_v') != 1:
        return None
    character = info.get('pet_id')
    seq, result = info.get('pet_seq'), info.get('pet_result')
    if (not valid_id(character) or type(seq) is not int or not 0 <= seq <= 0xffffffff
            or type(result) is not int or result not in (0, 1, 2, 3)):
        raise ValueError('Invalid Pet acknowledgement fields')
    nonce = bytes.fromhex(info['nonce'])
    if len(nonce) != 8:
        raise ValueError('Invalid Pet acknowledgement nonce')
    encoded = character.encode('ascii')
    head = b'XQPET' + nonce + struct.pack('<IBB', seq, result, len(encoded)) + encoded
    proof = info.get('pet_proof')
    if not isinstance(proof, str) or not hmac.compare_digest(hmac.digest(key, head, 'sha256').hex(), proof):
        raise ValueError('Pet acknowledgement authentication failed')
    return {'character_id': character, 'sequence': seq, 'result': result}


def desired_pet(payload, wall_now):
    if not isinstance(payload, dict) or payload.get('hub_alive') is not True:
        return None
    stamp = payload.get('generated_at')
    if type(stamp) not in (float, int) or not math.isfinite(stamp) or not wall_now - 30 < stamp <= wall_now + 5:
        return None
    pet = payload.get('pet')
    if not isinstance(pet, dict) or not valid_id(pet.get('character_id')):
        return None
    revision = pet.get('revision')
    # Revision zero is an initial default, not an explicit user selection.
    if type(revision) is not int or not 0 < revision <= 0x7fffffffffffffff:
        return None
    return pet['character_id'], revision


class PetRelay:
    """One send per explicit Hub revision/device nonce, bounded acknowledgement polls.

    Keep this object across BLE reconnects. Operations remain sequential with the
    existing status/catalogue writers, so shared packet sequences cannot race.
    """
    def __init__(self, logger=None, io_timeout=IO_TIMEOUT):
        self.io_timeout = io_timeout
        self.attempted = OrderedDict()
        self.pending = None
        self.last_poll = float('-inf')
        self.last_result = None
        self.logger = logger or (lambda text: print(text, flush=True))

    async def step(self, client, config, payload, read_info, now=None, wall_now=None):
        now = time.monotonic() if now is None else now
        desired = desired_pet(payload, time.time() if wall_now is None else wall_now)
        if self.pending is None and desired is None:
            return None
        if now - self.last_poll < POLL_SECONDS:
            return None
        self.last_poll = now
        info = await asyncio.wait_for(read_info(client, config), self.io_timeout)
        if info.get('pet_v') != 1:
            self.pending = None
            return None
        key = bytes.fromhex(config['key'])
        ack = verify_pet_info(info, key)
        nonce = info['nonce']
        if self.pending is not None:
            pending = self.pending
            if pending['nonce'] != nonce:
                # Reboot has a fresh authenticated nonce; resend desired once.
                self.pending = None
            elif ack['sequence'] == pending['sequence']:
                # Firmware reports the actual active character even on rejection.
                if ack['result'] == 3 or (ack['result'] == 2 and ack['character_id'] == pending['character_id']):
                    self.last_result = dict(pending, status='applied' if ack['result'] == 2 else 'rejected')
                    self.pending = None
                    self.logger('Pet ' + ('loaded: ' if ack['result'] == 2 else 'not loaded; keeping previous character: ') + pending['character_id'])
                    return self.last_result
            if self.pending is not None:
                if now - pending['started'] < RESULT_TIMEOUT:
                    return None
                self.last_result = dict(pending, status='unconfirmed')
                self.pending = None
                self.logger('Pet loading unconfirmed; not retrying this selection automatically')
                return self.last_result
        if desired is None:
            return None
        character, revision = desired
        if revision <= self.attempted.get(nonce, 0):
            return None
        packet = make_pet_packet(key, info, character)
        # Claim before I/O: a transport timeout does not prove it was not applied.
        self.attempted[nonce] = revision
        self.attempted.move_to_end(nonce)
        while len(self.attempted) > 64:
            self.attempted.popitem(last=False)
        self.pending = {'nonce': nonce, 'revision': revision, 'character_id': character,
                        'sequence': info['accepted'] + 1, 'started': now}
        await asyncio.wait_for(client.write_gatt_char(WRITE, packet, response=True), self.io_timeout)
        return None
