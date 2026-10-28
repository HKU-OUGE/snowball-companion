"""Offline Pet BLE framing, acknowledgement and nonblocking relay regression tests."""
import asyncio
import hmac
import struct
import unittest
from unittest.mock import AsyncMock
import pet_sync as p

KEY = bytes(range(32))
CONFIG = {'key': KEY.hex()}
NONCE = '0123456789abcdef'


def info(seq=0, result=0, character='snowball', nonce=NONCE, accepted=10):
    encoded = character.encode('ascii')
    raw = b'XQPET' + bytes.fromhex(nonce) + struct.pack('<IBB', seq, result, len(encoded)) + encoded
    return dict(pet_v=1, pet_id=character, pet_seq=seq, pet_result=result,
                pet_proof=hmac.digest(KEY, raw, 'sha256').hex(), nonce=nonce, accepted=accepted)


def payload(revision=1, character='ragdoll'):
    return dict(hub_alive=True, generated_at=1000, pet=dict(character_id=character, revision=revision))


class ProtocolTests(unittest.TestCase):
    def test_packet_exact_layout_and_signature(self):
        packet = p.make_pet_packet(KEY, info(), 'ragdoll')
        self.assertEqual(struct.unpack('<BBBI8s', packet[:15]), (4, 0, 0, 11, bytes.fromhex(NONCE)))
        self.assertEqual(packet[15:-32], b'\x07ragdoll')
        self.assertEqual(packet[-32:], hmac.digest(KEY, packet[:-32], 'sha256'))
    def test_invalid_id_and_overflow_rejected(self):
        for name in ('../x', '雪球', '', 'a'*32, 'Snowball'):
            with self.assertRaises(ValueError): p.make_pet_packet(KEY, info(), name)
        with self.assertRaises(ValueError): p.make_pet_packet(KEY, info(accepted=0xffffffff), 'snowball')
    def test_proof_tampering_is_rejected(self):
        original = info(11, 2, 'ragdoll')
        for field, value in [('pet_id', 'snowball'), ('pet_seq',12),('pet_result',3),('nonce','1111111111111111'),('pet_proof','')]:
            with self.assertRaises(ValueError): p.verify_pet_info(dict(original, **{field:value}), KEY)
        self.assertEqual(p.verify_pet_info(original, KEY)['result'], 2)
    def test_only_explicit_fresh_hub_selection(self):
        self.assertIsNone(p.desired_pet(payload(0), 1000))
        self.assertIsNone(p.desired_pet(payload(), 1040))
        self.assertIsNone(p.desired_pet(dict(payload(), hub_alive=False), 1000))
        self.assertIsNone(p.desired_pet(payload(True), 1000))
        self.assertEqual(p.desired_pet(payload(), 1000), ('ragdoll',1))


class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.logs=[];self.relay=p.PetRelay(self.logs.append)
        self.client=AsyncMock();self.reader=AsyncMock(return_value=info())
    async def step(self, revision=1, now=0, character='ragdoll'):
        return await self.relay.step(self.client,CONFIG,payload(revision,character),self.reader,now=now,wall_now=1000)
    async def test_old_firmware_and_revision_zero_never_write(self):
        await self.step(0);self.reader.assert_not_awaited()
        self.reader.return_value={'nonce':NONCE,'accepted':10}
        await self.step();self.client.write_gatt_char.assert_not_awaited()
    async def test_sends_once_then_pending_does_not_wait_for_sd(self):
        await self.step();self.assertEqual(self.client.write_gatt_char.await_count,1)
        self.reader.return_value=info(11,1,'snowball',accepted=11)
        result=await asyncio.wait_for(self.step(now=1),.1)
        self.assertIsNone(result);self.assertEqual(self.client.write_gatt_char.await_count,1)
        self.reader.return_value=info(11,2,'ragdoll',accepted=11)
        result=await self.step(now=2);self.assertEqual(result['status'],'applied')
        await self.step(now=3);self.assertEqual(self.client.write_gatt_char.await_count,1)
    async def test_wrong_sequence_or_success_character_never_acknowledges(self):
        await self.step()
        for n, result in enumerate((info(12,2,'ragdoll'),info(11,2,'snowball'))):
            self.reader.return_value=result;self.assertIsNone(await self.step(now=n+1))
        self.assertIsNotNone(self.relay.pending);self.assertIsNone(self.relay.last_result)
    async def test_signed_rejection_reports_old_active_and_no_resend(self):
        await self.step();self.reader.return_value=info(11,3,'snowball')
        result=await self.step(now=1);self.assertEqual(result['status'],'rejected')
        await self.step(now=2);self.assertEqual(self.client.write_gatt_char.await_count,1)
    async def test_transport_unknown_does_not_retry_same_revision(self):
        self.client.write_gatt_char.side_effect=TimeoutError
        with self.assertRaises(TimeoutError): await self.step()
        self.client.write_gatt_char.side_effect=None
        result=await self.step(now=31);self.assertEqual(result['status'],'unconfirmed')
        await self.step(now=32);self.assertEqual(self.client.write_gatt_char.await_count,1)
    async def test_local_device_change_not_overwritten_but_new_revision_sent(self):
        await self.step();self.reader.return_value=info(11,2,'ragdoll',accepted=11)
        await self.step(now=1);self.reader.return_value=info(11,2,'snowball',accepted=12)
        await self.step(now=2);self.assertEqual(self.client.write_gatt_char.await_count,1)
        await self.step(2,now=3);self.assertEqual(self.client.write_gatt_char.await_count,2)
        self.assertEqual(struct.unpack_from('<I',self.client.write_gatt_char.call_args.args[1],3)[0],13)
    async def test_new_nonce_allows_reissue_and_invalid_proof_never_writes(self):
        await self.step();self.reader.return_value=info(nonce='1111111111111111')
        await self.step(now=1);self.assertEqual(self.client.write_gatt_char.await_count,2)
        self.reader.return_value=dict(info(),pet_proof='bad')
        with self.assertRaises(ValueError): await self.step(2,now=2)
        self.assertEqual(self.client.write_gatt_char.await_count,2)
    async def test_new_revision_queued_while_current_loading(self):
        await self.step();self.reader.return_value=info(11,1,'snowball')
        await self.step(2,now=1,character='orange-cat');self.assertEqual(self.client.write_gatt_char.await_count,1)
        self.reader.return_value=info(11,2,'ragdoll',accepted=11)
        await self.step(2,now=2,character='orange-cat')
        await self.step(2,now=3,character='orange-cat');self.assertEqual(self.client.write_gatt_char.await_count,2)
    async def test_pending_poll_throttled(self):
        await self.step();await self.step(now=.2)
        self.assertEqual(self.reader.await_count,1)


if __name__=='__main__':unittest.main()
