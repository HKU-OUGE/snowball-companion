import asyncio
import hashlib
import hmac
import json
from pathlib import Path
import struct
import tempfile
import time
import unittest
from unittest.mock import patch, AsyncMock
import bridge
from status_hub import StatusHub, Invalid


class ClockedAsyncCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Tests deliberately control monotonic time for lease boundaries.
        asyncio.get_running_loop().set_debug(False)


class HubFocusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.now = time.time()
        self.hub = StatusHub(Path(self.tmp.name), lambda: self.now)

    def tearDown(self): self.tmp.cleanup()

    def event(self, session, provider='Codex', phase='running', seq=1, source='local'):
        self.hub.accept(dict(version=1, provider=provider, source=source, session_id=session,
                             event_id=f'{session}-{seq}', seq=seq, timestamp=self.now,
                             kind='task', phase=phase, display_name='会话 ' + session))

    def test_pinned_focus_not_stolen_and_auto_provider_filter(self):
        self.event('one'); self.now += 1; self.event('two')
        self.hub.set_focus({'source': 'local', 'session_id': 'one'})
        self.now += 1; self.event('three', 'Claude')
        self.assertEqual(self.hub.snapshot()['session_id'], 'one')
        self.hub.set_focus({'auto': True, 'provider': 'Codex'})
        self.assertEqual(self.hub.snapshot()['session_id'], 'one')
        self.now += 1; self.event('four', 'Claude')
        self.assertEqual(self.hub.snapshot()['session_id'], 'one')
        self.event('new')
        self.assertEqual(self.hub.snapshot()['session_id'], 'one')

    def test_active_preferred_and_stale_selection_rejected(self):
        self.event('active'); self.now += 1; self.event('done', phase='completed')
        self.assertEqual(self.hub.snapshot()['session_id'], 'active')
        self.now += 31
        with self.assertRaises(Invalid): self.hub.set_focus({'source':'local','session_id':'active'})
        self.assertEqual(self.hub.snapshot()['status'], 'unknown')

    def test_finished_pin_releases_after_completion_and_restart(self):
        self.event('one'); self.hub.set_focus({'source':'local','session_id':'one'})
        self.event('one', phase='completed', seq=2); self.event('two')
        reloaded = StatusHub(Path(self.tmp.name), lambda:self.now)
        self.assertEqual(reloaded.snapshot()['session_id'], 'two')
        self.assertFalse(reloaded.snapshot()['focus_pinned'])

    def test_catalog_namespace_capacity_and_active_priority(self):
        for i in range(12): self.event(str(i))
        self.event('0', source='other')
        relay = bridge.CatalogRelay()
        rows,total,pinned,focus = relay.catalogue(self.hub.snapshot(),'Codex',self.now)
        self.assertEqual((len(rows),total),(8,13))
        self.assertEqual(len({r['wire_id'] for r in rows}),8)
        self.assertNotEqual(bridge.wire_id({'source':'a','session_id':'0'*32}), bridge.wire_id({'source':'b','session_id':'0'*32}))
        self.assertTrue(all(r['active'] for r in rows))

    def test_stale_hub_empty(self):
        rows,total,*_ = bridge.CatalogRelay().catalogue({'hub_alive':False},'Codex',self.now)
        self.assertEqual((rows,total),([],0))


class WireTest(unittest.TestCase):
    def setUp(self):
        self.key=bytes(range(32)); self.config={'key':self.key.hex(),'device':'watch'}
        self.info={'nonce':'0011223344556677','accepted':5,'catalog_v':1}

    def request(self, **kwargs):
        r=dict(seq=1,provider=0,generation=1,pending=True,id='0'*32);r.update(kwargs)
        raw=struct.pack('<8sIBIB16s',bytes.fromhex(self.info['nonce']),r['seq'],r['provider'],r['generation'],r['pending'],bytes.fromhex(r['id']) if r['id'] else bytes(16))
        r['proof']=hmac.digest(self.key,raw,'sha256').hex();return dict(self.info,request=r)

    def test_signed_reverse_request_and_tamper(self):
        info=self.request();self.assertEqual(bridge.verify_request(info,self.config)['seq'],1)
        for name,value in [('provider',1),('seq',2),('generation',2),('id','f'*32),('pending',False)]:
            changed=json.loads(json.dumps(info));changed['request'][name]=value
            with self.assertRaises(ValueError):bridge.verify_request(changed,self.config)
        info['nonce']='f'*16
        with self.assertRaises(ValueError):bridge.verify_request(info,self.config)

    def test_v3_header_hmac_max_title(self):
        body=struct.pack('<IB16sBBB',12,0,bytes(16),2,20,192)+('汉'*64).encode()
        packet=bridge.make_catalog_packet(self.key,self.info,'Codex',1,body)
        self.assertEqual(len(packet),263)
        self.assertEqual(packet[:7],struct.pack('<BBBI',3,0,1,6))
        self.assertEqual(packet[-32:],hmac.digest(self.key,packet[:-32],'sha256'))


class RelayTest(ClockedAsyncCase):
    async def test_sidebar_metadata_delta_and_lease_renewal_preserve_directory(self):
        relay=bridge.CatalogRelay();info={'nonce':'0'*16,'accepted':0,'catalog_v':3}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock();now=time.time()
        row=dict(provider='Codex',source='codex-hooks',session_id='one',phase='running',
                 display_name='历史会话',sidebar_visible=True,source_fresh=True,task_fresh=True,
                 expires_at=now+100,task_expires_at=now+100)
        payload={'version':1,'hub_alive':True,'generated_at':now,'sessions':[row],'can_create':True}
        async def step(t):
            with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=None),patch.object(bridge.time,'monotonic',return_value=t):
                await relay.step(client,{'key':'0'*64},payload)
            packets=[call.args[1] for call in client.write_gatt_char.await_args_list]
            client.write_gatt_char.reset_mock();return packets
        initial=await step(100)
        self.assertEqual([p[2] for p in initial if p[1]==0],[0,1,2])
        row['phase']='thinking'
        updated=await step(102)
        self.assertEqual([p[2] for p in updated],[4])
        self.assertEqual(updated[0][15+33],bridge.STATES.index('thinking'))
        renewed=await step(111)
        self.assertEqual([p[2] for p in renewed],[3,3,3])
        row['display_name']='改名后的会话'
        renamed=await step(113)
        self.assertEqual([p[2] for p in renamed],[0,1,2])

    async def test_unchanged_catalog_only_refresh_after_bound(self):
        relay=bridge.CatalogRelay();info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=None),patch.object(bridge.time,'monotonic',return_value=100):
            await relay.step(client,{'key':'0'*64},{'version':1,'hub_alive':True,'sessions':[]})
        self.assertEqual(client.write_gatt_char.await_count,6) # begin+commit per provider
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=None),patch.object(bridge.time,'monotonic',return_value=102):
            await relay.step(client,{'key':'0'*64},{'version':1,'hub_alive':True,'sessions':[]})
        self.assertEqual(client.write_gatt_char.await_count,6)
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=None),patch.object(bridge.time,'monotonic',return_value=109):
            await relay.step(client,{'key':'0'*64},{'version':1,'hub_alive':True,'sessions':[]})
        self.assertEqual(client.write_gatt_char.await_count,12)

    async def test_unknown_or_old_generation_never_posts_focus(self):
        relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.generations=[1,1,1];relay.sent_at=[100]*3
        info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
        request={'seq':1,'provider':0,'generation':0,'pending':True,'id':'1'*32}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=102),patch.object(bridge,'focus_http') as post:
            await relay.step(client,{'key':'0'*64},{'version':1,'hub_alive':True,'sessions':[]})
            post.assert_not_called()
        self.assertEqual(relay.results[0],(1,2))


if __name__=='__main__': unittest.main()

class FocusRoundtripTest(ClockedAsyncCase):
    async def test_success_pinned_ack_and_duplicate_request_is_not_reposted(self):
        relay=bridge.CatalogRelay(); relay.nonce='0'*16; relay.generations=[1,1,1]; relay.sent_at=[100]*3
        info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
        now=time.time()
        entry=dict(provider='Codex',source='local',session_id='one',phase='running',source_fresh=True,task_fresh=True,expires_at=now+100,task_expires_at=now+100)
        rid=bridge.wire_id(entry)
        row=dict(entry,wire_id=rid,active=True,expiry=now+100)
        relay.sent_catalogs[(0,1)]={'sent_at':100,'entries':{rid:row}}
        request={'seq':1,'provider':0,'generation':1,'pending':True,'id':rid}
        payload={'version':1,'hub_alive':True,'sessions':[entry],'focus_pinned':True,'focused_session':'local/one'}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=102),patch.object(bridge,'focus_http',return_value=payload) as post:
            result=await relay.step(client,{'key':'0'*64},payload)
            post.assert_called_once_with({'source':'local','session_id':'one'})
            self.assertEqual(result,payload)
            self.assertEqual(relay.results[0],(1,1))
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=104),patch.object(bridge,'focus_http') as post:
            await relay.step(client,{'key':'0'*64},payload)
            post.assert_not_called()

    async def test_expired_pending_on_final_read_does_not_post(self):
        relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.generations=[1,1,1];relay.sent_at=[100]*3
        info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
        request={'seq':1,'provider':0,'generation':1,'pending':True,'id':''}
        relay.sent_catalogs[(0,1)]={'sent_at':100,'entries':{}}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',side_effect=[request,dict(request,pending=False)]),patch.object(bridge.time,'monotonic',return_value=102),patch.object(bridge,'focus_http') as post:
            await relay.step(client,{'key':'0'*64},{'version':1,'hub_alive':True,'sessions':[]})
            post.assert_not_called()
            self.assertEqual(relay.results[0],(1,2))


class CatalogRaceTest(ClockedAsyncCase):
    async def test_click_previous_generation_keeps_exact_source_identity(self):
        now=time.time()
        entry=dict(provider='Codex',source='original',session_id='one',phase='running',source_fresh=True,task_fresh=True,expires_at=now+100,task_expires_at=now+100)
        rid=bridge.wire_id(entry)
        row=dict(entry,wire_id=rid,active=True,expiry=now+100)
        relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.generations=[2,1,1];relay.sent_at=[101]*3
        relay.sent_catalogs[(0,1)]={'sent_at':100,'entries':{rid:row}}
        relay.sent_catalogs[(0,2)]={'sent_at':101,'entries':{}}
        request={'seq':1,'provider':0,'generation':1,'pending':True,'id':rid}
        info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
        payload={'version':1,'hub_alive':True,'sessions':[entry],'focus_pinned':True,'focused_session':'original/one'}
        client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
        with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=103),patch.object(bridge,'focus_http',return_value=payload) as post:
            await relay.step(client,{'key':'0'*64},payload)
            post.assert_called_once_with({'source':'original','session_id':'one'})
            self.assertEqual(relay.results[0],(1,1))

    async def test_previous_generation_past_lease_or_now_terminal_is_rejected(self):
        now=time.time()
        for sent_at,phase in [(70,'running'),(100,'completed')]:
            with self.subTest(sent_at=sent_at,phase=phase):
                entry=dict(provider='Codex',source='original',session_id='one',phase=phase,source_fresh=True,task_fresh=True,expires_at=now+100,task_expires_at=now+100)
                rid=bridge.wire_id(entry)
                row=dict(entry,phase='running',wire_id=rid,active=True,expiry=now+100)
                relay=bridge.CatalogRelay();relay.nonce='0'*16;relay.generations=[2,1,1];relay.sent_at=[101]*3
                relay.sent_catalogs[(0,1)]={'sent_at':sent_at,'entries':{rid:row}}
                request={'seq':1,'provider':0,'generation':1,'pending':True,'id':rid}
                info={'nonce':'0'*16,'accepted':0,'catalog_v':1}
                payload={'version':1,'hub_alive':True,'sessions':[entry]}
                client=unittest.mock.Mock();client.write_gatt_char=AsyncMock()
                with patch.object(bridge,'read_info',AsyncMock(return_value=info)),patch.object(bridge,'verify_request',return_value=request),patch.object(bridge.time,'monotonic',return_value=103),patch.object(bridge,'focus_http') as post:
                    await relay.step(client,{'key':'0'*64},payload)
                    post.assert_not_called()
                    self.assertEqual(relay.results[0],(1,2))


class EmptyFocusTest(unittest.TestCase):
    def test_auto_expired_empty_but_fresh_terminal_preserved(self):
        now=time.time()
        for expiry,expected in [(now-1,''),(now+20,'identity')]:
            entry=dict(provider='Codex',source='local',session_id='one',phase='completed',source_fresh=expiry>now,task_fresh=expiry>now,expires_at=expiry,task_expires_at=expiry)
            payload={'version':1,'hub_alive':True,'sessions':[entry],'focus_pinned':False,'focused_session':'local/one'}
            rows,total,pinned,focused=bridge.CatalogRelay().catalogue(payload,'Codex',now)
            self.assertFalse(pinned)
            self.assertEqual(focused,bridge.wire_id(entry) if expected else '')

    def test_info_maximum_fits_att_attribute_limit(self):
        info=dict(v=1,device='Xueqiu-ffffffff',nonce='f'*16,accepted=4294967295,applied=4294967295,provider=2,state=5,proof='f'*64,catalog_v=1,request=dict(seq=4294967295,provider=2,generation=4294967295,pending=False,id='f'*32,proof='f'*64))
        self.assertEqual(len(json.dumps(info,separators=(',',':')).encode()),413)
        self.assertLess(len(json.dumps(info,separators=(',',':')).encode()),512)
