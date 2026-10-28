"""BLE worker for one local, short-lived provisioning request."""
import asyncio
import json
import struct,hmac
import time
from pathlib import Path
import http.client
from network_security import NETWORK, seal_config, packets, verify_status


def control(path, data=None):
    token = (Path.home()/'.config/xueqiu/hub-token').read_text().strip()
    connection = http.client.HTTPConnection('127.0.0.1', 4194, timeout=2)
    body = None if data is None else json.dumps(data, separators=(',', ':'))
    try:
        connection.request('GET' if data is None else 'POST', path, body,
                           {'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        response = connection.getresponse()
        raw = response.read(12000)
        if response.status != 200:
            raise OSError('Network control unavailable')
        return json.loads(raw)
    finally:
        connection.close()


class NetworkProvision:
    def __init__(self):
        self.last = 0
        self.prefer_wifi = False
        self.maintenance = False

    @property
    def quiescent(self):
        # Keep the physical BLE connection and authenticated metadata probes,
        # while the bridge defers ordinary GATT snapshot/voice/power traffic.
        return self.prefer_wifi or self.maintenance

    async def read_status(self, client, config, info):
        key=bytes.fromhex(config['key'])
        if client.services.get_characteristic(NETWORK) is not None:
            raw=bytes(await client.read_gatt_char(NETWORK))
        else:
            body=struct.pack('<BBBI8s',9,0,3,info['accepted']+1,bytes.fromhex(info['nonce']))
            await client.write_gatt_char('9e7a0003-6ed4-4b6b-bf37-43dc42838c20',body+hmac.digest(key,body,'sha256'),response=True)
            raw=bytes(await client.read_gatt_char('9e7a0004-6ed4-4b6b-bf37-43dc42838c20'))
        return verify_status(raw,key,info['nonce'])

    async def step(self, client, config, read_info):
        if time.monotonic()-self.last < 2:
            return self.quiescent
        self.last = time.monotonic()
        info = await read_info(client, config)
        if not info.get('network_v'):
            self.prefer_wifi = self.maintenance = False
            return self.quiescent
        key = bytes.fromhex(config['key'])
        status = await self.read_status(client, config, info)
        self.prefer_wifi = status.get('active') is True
        # read_status verifies the device HMAC and current INFO nonce first.
        # OTA temporarily releases Wi-Fi snapshot ownership (active=false).
        self.maintenance = status.get('state') == 'updating'
        try:
            job = await asyncio.to_thread(control, '/v1/bridge', {'status': status})
        except (OSError, ValueError):
            return self.quiescent
        if not job.get('config'):
            return self.quiescent
        outcome = 'failed'
        maintenance_rejection = self.maintenance
        try:
            if maintenance_rejection:
                # Do not change Wi-Fi credentials/revision during installation.
                # The finally block cancels an unexpectedly claimed local job.
                return self.quiescent
            if not status.get('window'):
                outcome = 'open_device_network_page'
                return self.quiescent
            if info['device'] != job['config']['device']:
                outcome = 'device_mismatch'
                return self.quiescent
            info = await read_info(client, config)
            blob = seal_config(job['config'], key, info['nonce'])
            for packet in packets(blob, key, info):
                await asyncio.wait_for(client.write_gatt_char('9e7a0003-6ed4-4b6b-bf37-43dc42838c20', packet, response=True), 5)
            # Mode activation is deferred by the firmware until the transfer ends.
            info = await read_info(client, config)
            status = await self.read_status(client, config, info)
            outcome = 'stored' if status.get('sealed') else 'temporary'
            self.prefer_wifi = status.get('active') is True
            self.maintenance = status.get('state') == 'updating'
        finally:
            job.clear()
            try:
                await asyncio.to_thread(control, '/v1/result', {'result': outcome, 'status': status})
            except (OSError, ValueError):
                # A failed local rejection report must not disconnect BLE in
                # the middle of OTA. The unapplied gateway job expires in 60s.
                # Ordinary provisioning retains its existing error semantics.
                if not maintenance_rejection:
                    raise
        return self.quiescent
