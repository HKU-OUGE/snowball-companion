"""Compile the actual firmware catalogue branch on host; fake only RTOS/time.
Crypto framing has independent protocol tests; this exercises commit, expiry and focus ACK.
"""
import ctypes
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest


class NativeCatalog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('cc'): raise unittest.SkipTest('host C compiler unavailable')
        cls.tmp = tempfile.TemporaryDirectory()
        source = (Path(__file__).parents[1]/'main/xueqiu_ble.c').read_text()
        header = (Path(__file__).parents[1]/'main/xueqiu_ble.h').read_text()
        types = header[header.index('#define XQ_BLE_CATALOG_MAX'):header.index('/* Copies')]
        globals_ = source[source.index('static xq_ble_catalog_t'):source.index('void ble_store_config_init')]
        hexfn = source[source.index('static void hex('):source.index('static void speech_send_task')]
        parser = source[source.index('  if(packet[0]==3) {'):source.index('  xq_ble_update_t update =')]
        getters = source[source.index('bool xq_ble_catalog_get'):source.index('const char *xq_ble_device_name')]
        prefix = '#define EXT_RAM_BSS_ATTR\n#include "' + str((Path(__file__).parents[1]/'main/catalog_order.h').resolve()) + '"\n#include "' + str((Path(__file__).parents[1]/'main/attention_wire.h').resolve()) + '"\n' + '''#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define BLE_ATT_ERR_VALUE_NOT_ALLOWED 19
#define portENTER_CRITICAL(x) ((void)0)
#define portEXIT_CRITICAL(x) ((void)0)
static int lock;
static bool connected=true;
static uint32_t accepted_seq;
static int64_t clock_us=1000000;
static int64_t esp_timer_get_time(void){return clock_us;}
void set_clock(int64_t v){clock_us=v;}
'''
        program = prefix+types+globals_+hexfn+'''int ingest(const uint8_t *packet,int len){size_t signed_len=len-32;uint32_t seq=1;\n'''+parser+'return 99;}\n'+getters
        program += '''
void reset(void){memset(catalogs,0,sizeof(catalogs));memset(catalog_until,0,sizeof(catalog_until));staging_until=0;request_seq=0;request_status=0;request_action=0;request_id[0]=0;request_before[0]=0;clock_us=1000000;}
int count(void){return catalogs[0].count;}
int state(void){return catalogs[0].rows[0].state;}
uint32_t generation(void){return catalogs[0].generation;}
int empty_focus(void){return catalogs[0].focused_id[0]==0;}
int active(void){xq_ble_catalog_t c;xq_ble_catalog_get(0,&c);return c.rows[0].active;}
int valid(void){xq_ble_catalog_t c;return xq_ble_catalog_get(0,&c);}
int requested_action(void){return request_action;}
const char *requested_before(void){return request_before;}
uint32_t requested_generation(void){return request_gen;}
'''
        path=Path(cls.tmp.name);(path/'test.c').write_text(program)
        subprocess.run(['cc','-shared','-fPIC','-std=c11',str(path/'test.c'),'-o',str(path/'test.so')],check=True,capture_output=True)
        cls.lib=ctypes.CDLL(str(path/'test.so'));cls.lib.set_clock.argtypes=[ctypes.c_int64]
        cls.lib.ingest.argtypes=[ctypes.c_char_p,ctypes.c_int]
        cls.lib.xq_ble_request_focus.argtypes=[ctypes.c_uint8,ctypes.c_char_p]
        cls.lib.xq_ble_request_focus.restype=ctypes.c_bool
        cls.lib.xq_ble_request_move.argtypes=[ctypes.c_uint8,ctypes.c_char_p,ctypes.c_char_p]
        cls.lib.xq_ble_request_move.restype=ctypes.c_bool
        cls.lib.xq_ble_request_order.argtypes=[ctypes.c_uint8,ctypes.c_void_p,ctypes.c_uint8]
        cls.lib.xq_ble_request_order.restype=ctypes.c_bool
        cls.lib.requested_before.restype=ctypes.c_char_p

    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def setUp(self):self.lib.reset()
    def packet(self,op,body,p=0):
        raw=bytes([3,p,op])+bytes(12)+body+bytes(32)
        return self.lib.ingest(raw,len(raw))
    def begin(self,count=1,ack=0,flags=0,focus=bytes(16)):
        return self.packet(0,struct.pack('<IBHB16sIB',1,count,count,flags,focus,ack,20))
    def row(self,index=0,id=bytes.fromhex('1'*32),lease=10,title='会话',state=2):
        text=title.encode();return self.packet(1,struct.pack('<IB16sBBB',1,index,id,state,lease,len(text))+text)
    def commit(self):return self.packet(2,struct.pack('<I',1))
    def ready(self):
        self.assertEqual(self.begin(),0);self.assertEqual(self.row(),0);self.assertEqual(self.commit(),0)
    def order(self,ids,provider=0):
        rows=((ctypes.c_char*33)*len(ids))()
        for i,value in enumerate(ids):rows[i].value=value
        return self.lib.xq_ble_request_order(provider,rows,len(ids))

    def test_partial_catalog_does_not_publish_and_duplicate_row_rejected(self):
        self.assertEqual(self.begin(),0);self.assertFalse(self.lib.valid())
        self.assertEqual(self.commit(),19);self.assertEqual(self.lib.count(),0)
        self.assertEqual(self.row(),0);self.assertEqual(self.row(),19)
        self.assertEqual(self.commit(),0);self.assertEqual(self.lib.count(),1)

    def test_row_expiry_and_catalog_expiry(self):
        self.ready();self.assertTrue(self.lib.active());self.lib.set_clock(12000000)
        self.assertFalse(self.lib.active());self.assertTrue(self.lib.valid())
        self.lib.set_clock(22000000);self.assertFalse(self.lib.valid())

    def test_focus_only_confirmed_with_matching_identity(self):
        self.ready();self.assertTrue(self.lib.xq_ble_request_focus(0,b'1'*32));self.assertEqual(self.lib.xq_ble_focus_status(),1)
        self.begin(ack=1,flags=3,focus=bytes.fromhex('2'*32));self.row();self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),3)
        self.assertTrue(self.lib.xq_ble_request_focus(0,b'1'*32))
        self.begin(ack=2,flags=3,focus=bytes.fromhex('1'*32));self.row();self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),2)

    def test_request_expiry_and_unknown_identity(self):
        self.ready();self.assertFalse(self.lib.xq_ble_request_focus(0,b'2'*32))
        self.assertTrue(self.lib.xq_ble_request_focus(0,None));self.lib.set_clock(12000000)
        self.assertEqual(self.lib.xq_ble_focus_status(),3)

    def test_empty_auto_focus_ack_uses_empty_string_sentinel(self):
        self.begin(count=0);self.commit()
        self.assertTrue(self.lib.empty_focus())
        self.assertTrue(self.lib.xq_ble_request_focus(0,None))
        self.begin(count=0,ack=1,flags=2);self.commit()
        self.assertTrue(self.lib.empty_focus())
        self.assertEqual(self.lib.xq_ble_focus_status(),2)

    def test_reorder_completed_and_idle_rows_without_active_lease(self):
        self.begin(count=2)
        self.row(state=4,lease=0)
        self.row(index=1,id=bytes.fromhex('2'*32),state=0,lease=0)
        self.commit()
        self.assertFalse(self.lib.xq_ble_request_focus(0,b'1'*32))
        self.assertTrue(self.lib.xq_ble_request_move(0,b'2'*32,b'1'*32))
        self.assertEqual(self.lib.requested_action(),1)
        self.assertEqual(self.lib.requested_before(),b'1'*32)
        self.assertEqual(self.lib.requested_generation(),1)
        self.assertFalse(self.lib.xq_ble_request_move(0,b'1'*32,None))
        # An auto queue's chosen focus need not be the row being moved.
        self.begin(count=2,ack=1,flags=2,focus=bytes.fromhex('1'*32))
        self.row(index=0,id=bytes.fromhex('2'*32),state=0,lease=0)
        self.row(index=1,state=4,lease=0)
        self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),2)
        self.assertTrue(self.lib.xq_ble_request_move(0,b'2'*32,None))
        self.assertEqual(self.lib.requested_before(),b'')

    def test_reorder_rejects_unknown_self_expired_and_pinned_ack(self):
        self.ready()
        self.assertFalse(self.lib.xq_ble_request_move(0,b'2'*32,None))
        self.assertFalse(self.lib.xq_ble_request_move(0,b'1'*32,b'2'*32))
        self.assertFalse(self.lib.xq_ble_request_move(0,b'1'*32,b'1'*32))
        self.assertFalse(self.lib.xq_ble_request_move(3,b'1'*32,None))
        self.assertTrue(self.lib.xq_ble_request_move(0,b'1'*32,None))
        self.begin(ack=1,flags=3,focus=bytes.fromhex('1'*32));self.row();self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),3)
        self.lib.set_clock(22000000)
        self.assertFalse(self.lib.xq_ble_request_move(0,b'1'*32,None))

    def test_reorder_ack_needs_matching_sequence_and_success(self):
        self.ready()
        self.assertTrue(self.lib.xq_ble_request_move(0,b'1'*32,b''))
        self.begin(ack=9,flags=2);self.row();self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),1)
        self.begin(ack=1,flags=4);self.row();self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),3)

    def test_atomic_order_encodes_complete_permutation_once(self):
        self.begin(count=3)
        for i in range(3):self.row(index=i,id=bytes.fromhex(str(i+1)*32),state=4,lease=0)
        self.commit()
        self.assertTrue(self.order([b'3'*32,b'1'*32,b'2'*32]))
        self.assertEqual(self.lib.requested_action(),2)
        self.assertEqual(bytes.fromhex(self.lib.requested_before().decode()),bytes([3,2,0,1])+bytes(12))
        self.assertFalse(self.order([b'1'*32,b'2'*32,b'3'*32]))
        # One signed accepted batch ACK, in automatic mode, completes it.
        self.begin(count=3,ack=1,flags=2,focus=bytes.fromhex('1'*32))
        for i,n in enumerate([3,1,2]):self.row(index=i,id=bytes.fromhex(str(n)*32),state=4,lease=0)
        self.commit()
        self.assertEqual(self.lib.xq_ble_focus_status(),2)

    def test_atomic_order_rejects_incomplete_duplicate_unknown_and_expired(self):
        self.begin(count=2);self.row();self.row(index=1,id=bytes.fromhex('2'*32));self.commit()
        self.assertFalse(self.order([]))
        self.assertFalse(self.order([b'1'*32]))
        self.assertFalse(self.order([b'1'*32,b'1'*32]))
        self.assertFalse(self.order([b'1'*32,b'3'*32]))
        self.assertFalse(self.order([b'1'*32,b'2'*32],3))
        self.lib.set_clock(22000000)
        self.assertFalse(self.order([b'1'*32,b'2'*32]))

    def test_atomic_order_uses_latest_catalog_order(self):
        self.begin(count=2);self.row();self.row(index=1,id=bytes.fromhex('2'*32));self.commit()
        self.begin(count=2);self.row(id=bytes.fromhex('2'*32));self.row(index=1);self.commit()
        self.assertTrue(self.order([b'1'*32,b'2'*32]))
        self.assertEqual(bytes.fromhex(self.lib.requested_before().decode()),bytes([2,1,0])+bytes(13))

    def test_staging_timeout_and_full_utf8_title(self):
        self.begin();self.assertEqual(self.row(title='汉'*64),0);self.lib.set_clock(32000000)
        self.assertEqual(self.commit(),19)
        self.begin();self.assertEqual(self.row(title='汉'*64),0);self.assertEqual(self.commit(),0)



    def test_full_64_row_catalog_and_packed_atomic_order(self):
        from bridge import decode_order
        self.begin(count=64)
        for i in range(64):self.row(index=i,id=(i+1).to_bytes(16,'big'),lease=0,state=0)
        self.assertEqual(self.commit(),0);self.assertEqual(self.lib.count(),64)
        self.assertTrue(self.order([f'{i+1:032x}'.encode() for i in reversed(range(64))]))
        self.assertEqual(self.lib.requested_action(),12)
        packed=self.lib.requested_before().decode();self.assertEqual(len(packed),98)
        self.assertEqual(decode_order(packed),list(reversed(range(64))))

    def test_keepalive_does_not_replace_catalog_or_lose_active_expiry(self):
        self.ready();self.lib.set_clock(9000000)
        self.assertEqual(self.packet(3,struct.pack('<IBB',1,1,20)),0)
        self.lib.set_clock(18000000);self.assertTrue(self.lib.valid());self.assertTrue(self.lib.active())
        self.assertEqual(self.packet(3,struct.pack('<IBB',2,1,20)),19)
        self.assertEqual(self.packet(3,struct.pack('<IBB',1,2,20)),19)
        self.assertEqual(self.packet(3,struct.pack('<IBB',1,1,26)),19)
        self.lib.set_clock(31000000);self.assertFalse(self.lib.valid());self.assertFalse(self.lib.active())

    def test_metadata_delta_retains_identity_and_confirms_focus(self):
        self.ready();self.assertTrue(self.lib.xq_ble_request_focus(0,b'1'*32))
        header=struct.pack('<IIBHB16sIB',1,2,1,1,3,bytes.fromhex('1'*32),1,20)
        self.assertEqual(self.packet(4,header+bytes([1,20])),0)
        self.assertEqual(self.lib.generation(),2);self.assertEqual(self.lib.state(),1)
        self.assertEqual(self.lib.count(),1);self.assertEqual(self.lib.xq_ble_focus_status(),2)
        self.assertTrue(self.lib.xq_ble_request_focus(0,b'1'*32))

    def test_metadata_delta_is_atomic_and_generation_bound(self):
        self.ready()
        for base,count,state,lease in [(2,1,1,20),(1,2,1,20),(1,1,6,20),(1,1,1,26)]:
            header=struct.pack('<IIBHB16sIB',base,2,count,1,0,bytes(16),0,20)
            self.assertEqual(self.packet(4,header+bytes([state,lease])),19)
            self.assertEqual(self.lib.generation(),1);self.assertEqual(self.lib.state(),2)
