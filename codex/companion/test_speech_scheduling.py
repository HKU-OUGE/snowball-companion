"""Voice fast path keeps live status and pending questions in every iteration."""
import tempfile
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock,Mock,patch

import bridge


class Connection:
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass


class Scheduling(unittest.IsolatedAsyncioTestCase):
    async def test_upload_defers_bulk_but_keeps_live_status_and_attention(self):
        with tempfile.TemporaryDirectory() as tmp,ExitStack() as stack:
            path=Path(tmp)/'snapshot.json'
            path.write_text('{"provider":"Codex","status":"running","marker":"live"}')
            args=types.SimpleNamespace(command='watch',source=path,provider='Codex',state='idle',quit=False)
            status=Mock(step=AsyncMock(return_value=None))
            catalog=Mock(step=AsyncMock(return_value=None))
            attention=Mock(step=AsyncMock(return_value=None))
            summaries=Mock(step=AsyncMock(return_value=None))
            pets=Mock(step=AsyncMock(return_value=None))
            power=Mock(step=AsyncMock(return_value=None))
            calls=[]
            class Speech:
                transferring=True
                latency_sensitive=True
                async def close(self):pass
                async def step(self,client,config,payload):
                    calls.append(payload)
                    if len(calls)==3:args.quit=True
            stack.enter_context(patch('bleak.BleakClient',return_value=Connection()))
            stack.enter_context(patch.object(bridge,'find_device',AsyncMock(return_value=object())))
            stack.enter_context(patch.object(bridge,'linux_agent',AsyncMock(return_value=None)))
            stack.enter_context(patch.object(bridge,'read_info',AsyncMock(return_value={})))
            stack.enter_context(patch.object(bridge,'parse_source',side_effect=lambda p:bridge.SourceStatus('Codex','running','hub',None)))
            for name,value in [('StatusRelay',status),('CatalogRelay',catalog)]:
                stack.enter_context(patch.object(bridge,name,return_value=value))
            for name,value in [('attention_relay.AttentionRelay',attention),('attention_relay.SummaryRelay',summaries),
                               ('pet_sync.PetRelay',pets),('power_telemetry.PowerRelay',power)]:
                stack.enter_context(patch(name,return_value=value))
            stack.enter_context(patch('speech_relay.SpeechRelay',return_value=Speech()))
            stack.enter_context(patch('network_provision.NetworkProvision',return_value=Mock(step=AsyncMock(return_value=False))))
            await bridge.connected_session(args,{'device':'test'})
            self.assertEqual([p['marker'] for p in calls],['live']*3)
            self.assertEqual([c.args[2].state for c in status.step.call_args_list],['running']*3)
            self.assertEqual(attention.step.await_count,3)
            self.assertEqual(catalog.step.await_count,1)
            self.assertEqual(summaries.step.await_count,1)
            self.assertEqual(pets.step.await_count,1)


if __name__=='__main__':unittest.main()
