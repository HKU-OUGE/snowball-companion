"""Isolated Windows supervisor contracts; no live tasks, BLE or processes used.

Win32 APIs are replaced at their handle boundary on non-Windows hosts. Kernel
Job Objects, ACLs, Task Scheduler and real BLE still require Windows validation.
"""
from contextlib import ExitStack, redirect_stderr
import base64
import ctypes
import io
import json
import os
from pathlib import Path
import subprocess
import socket
import threading
import time
import urllib.request
import urllib.error
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import xml.etree.ElementTree as ET

import windows_runtime as runtime
ORIGINAL_LAUNCH_PREFIX=runtime.launch_prefix

class Pipe(io.BytesIO):
    def close(self):self.closed_by_parent=True

class Process:
    def __init__(self,pid):
        self.pid=pid;self._handle=pid+10000;self.stdin=Pipe();self.returncode=None;self.terminated=0
    def poll(self):return self.returncode
    def terminate(self):self.terminated+=1;self.returncode=0
    def wait(self,timeout=None):
        if self.returncode is None:raise subprocess.TimeoutExpired('owned fixture',timeout)
        return self.returncode

class API:
    def __init__(self):
        self.me={'pid':42,'start':'100','exe':'fixture.exe','sid':'S-1-5-21-123','session':1}
        self.identities={42:dict(self.me)};self.assigned=[];self.closed=[];self.fail_assign=False;self.gate_ok=False
    def current(self):return dict(self.me)
    def identity(self,pid):return self.identities.get(pid)
    def job(self):return 9
    def assign(self,job,p):
        if self.fail_assign:raise OSError('fixture assignment failure')
        self.assigned.append(p)
    def close_job(self,job):
        self.closed.append(job)
        for p in self.assigned:p.terminate()
    def gate(self):return self.gate_ok
    def host_job_context(self):return {'in_job':False}
    def account_sid(self,name):raise ValueError('No such fixture account')
    def task_tool(self):return str(Path(self.me['exe']).absolute().parent/'System32'/'schtasks.exe')

class WindowsRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'runtime';self.api=API();self.clock=[100.]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        for name,value in [('DIRECTORY',self.directory),('_api_instance',self.api),('_windows',lambda:True)]:
            self.stack.enter_context(patch.object(runtime,name,value))
        self.prefix=['fixture.exe','runtime']
        self.stack.enter_context(patch.object(runtime,'launch_prefix',return_value=self.prefix))
    def record(self,**changes):
        value={'v':1,'install':runtime._install_id(),'identity':dict(self.api.me),'port':4199,'token':'ab'*32}
        value.update(changes)
        from platform_support import ensure_private_dir
        ensure_private_dir(self.directory);runtime._write_record(value)
        return value
    def test_single_instance_lifetime_lock(self):
        with runtime._lock('supervisor.lock',nonblocking=True):
            with self.assertRaises(BlockingIOError):
                with runtime._lock('supervisor.lock',nonblocking=True):pass
        with runtime._lock('supervisor.lock',nonblocking=True):pass
    def test_start_cannot_spawn_when_unknown_supervisor_holds_lock(self):
        with runtime._lock('supervisor.lock',nonblocking=True),patch.object(runtime.subprocess,'Popen') as spawn:
            with self.assertRaises(RuntimeError):runtime.start()
            spawn.assert_not_called()
    def test_running_start_is_idempotent(self):
        result={'running':True,'state':'running','children':{}}
        with patch.object(runtime,'status',return_value=result),patch.object(runtime.subprocess,'Popen') as spawn:
            self.assertIs(runtime.start(),result);spawn.assert_not_called()
    def test_pid_reuse_never_authorizes_stop_or_control(self):
        self.record();self.api.identities[42]['start']='101'
        with patch.object(runtime,'_rpc') as rpc:
            self.assertEqual(runtime.status()['state'],'unverified')
            with self.assertRaises(RuntimeError):runtime.stop()
            rpc.assert_not_called()
        self.assertTrue(runtime._record_path().exists())
    def test_wrong_executable_sid_or_session_is_unverified(self):
        for key,value in [('exe','different.exe'),('sid','S-1-5-21-other'),('session',2)]:
            with self.subTest(key=key):
                self.record();self.api.identities[42]=dict(self.api.me,**{key:value})
                self.assertEqual(runtime.status()['state'],'unverified')
                with self.assertRaises(RuntimeError):runtime.stop()
    def test_record_wrong_install_and_extra_fields_fail_closed(self):
        self.record(install='different')
        self.assertEqual(runtime.status()['state'],'unverified')
        self.record(unexpected='not accepted');self.assertEqual(runtime.status()['state'],'unverified')
    def test_status_whitelist_does_not_return_control_token(self):
        self.record()
        result={'supported':True,'running':True,'state':'running','install':runtime._install_id(),'children':{},'token':'private','identity':self.api.me}
        with patch.object(runtime,'_rpc',return_value=result):
            shown=runtime.status();self.assertNotIn('token',shown);self.assertNotIn('identity',shown)
    def test_stop_authenticates_and_waits_identity_not_pid(self):
        self.record()
        def rpc(rec,op):
            self.assertEqual(op,'stop');self.api.identities[42]=dict(self.api.me,start='900');return {}
        with patch.object(runtime,'_rpc',side_effect=rpc):self.assertEqual(runtime.stop()['state'],'stopped')
    def test_stop_and_autostart_remove_preserve_device_credentials(self):
        credentials=Path(self.temp.name)/'pairing.json';credentials.write_bytes(b'private fixture sentinel')
        self.assertEqual(runtime.stop()['state'],'stopped')
        with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0x80070002,stdout='')):
            runtime.remove_autostart()
        self.assertEqual(credentials.read_bytes(),b'private fixture sentinel')
    def supervisor(self):
        self.processes=[]
        def popen(argv,**kwargs):
            self.assertEqual(argv[:2],self.prefix);self.assertEqual(kwargs['stdin'],subprocess.PIPE)
            p=Process(100+len(self.processes));self.processes.append(p);return p
        self.stack.enter_context(patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()))
        self.stack.enter_context(patch.object(runtime,'_hub_health',return_value=True))
        return runtime.Supervisor(self.prefix,clock=lambda:self.clock[0],popen=popen)
    def test_children_gate_opens_only_after_owned_job_assignment(self):
        supervisor=self.supervisor();supervisor.step()
        self.assertEqual(len(self.processes),1);self.assertEqual(self.api.assigned,self.processes)
        self.assertEqual(self.processes[0].stdin.getvalue(),b'G')
        self.clock[0]+=1;supervisor.step();supervisor.step()
        self.assertEqual(len(self.processes),2);self.assertEqual(supervisor.snapshot()['children']['bridge']['state'],'running')
        self.assertEqual(supervisor.snapshot()['children']['bridge']['restarts'],0)
        supervisor.close();self.assertTrue(all(p.terminated for p in self.processes))
    def test_failed_job_assignment_never_opens_child_gate(self):
        supervisor=self.supervisor();self.api.fail_assign=True;supervisor.step()
        self.assertEqual(self.processes[0].stdin.getvalue(),b'');self.assertEqual(self.processes[0].terminated,1)
        self.assertEqual(supervisor.snapshot()['children']['hub']['state'],'backoff');supervisor.close()
    def test_child_crashes_restart_with_exponential_bounded_backoff(self):
        supervisor=self.supervisor();supervisor.step();child=supervisor.children['hub']
        for failure in range(1,9):
            child['process'].returncode=1;supervisor.step()
            delay=min(60,2**(failure-1));self.assertEqual(child['next'],self.clock[0]+delay)
            self.clock[0]+=delay-.1;supervisor.step();self.assertIsNone(child['process'])
            self.clock[0]+=.1;supervisor.step();self.assertIsNotNone(child['process'])
        supervisor.close()
    def test_hub_loss_stops_only_own_bridge_and_gates_restart(self):
        outsider=Process(999);supervisor=self.supervisor();supervisor.step();self.clock[0]+=1;supervisor.step();supervisor.step()
        bridge=supervisor.children['bridge']['process'];supervisor.children['hub']['process'].returncode=1;supervisor.step()
        self.assertEqual(bridge.terminated,1);self.assertEqual(outsider.terminated,0)
        self.assertEqual(supervisor.children['bridge']['state'],'waiting_hub');supervisor.close();self.assertEqual(outsider.terminated,0)
    def test_child_eof_gate_does_not_import_or_execute_hub(self):
        with patch.object(runtime.importlib,'import_module') as load:
            self.assertEqual(runtime.child('hub'),2);load.assert_not_called()
    def test_task_xml_is_interactive_limited_and_current_user(self):
        sid=self.api.me['sid'];xml=runtime._task_xml(self.prefix,sid)
        self.assertTrue(runtime._owned_task(xml,self.prefix,sid))
        self.assertIn('InteractiveToken',xml);self.assertIn('LeastPrivilege',xml)
        self.assertNotIn('Password',xml);self.assertNotIn('HighestAvailable',xml)
        self.assertFalse(runtime._owned_task(xml,self.prefix,'S-1-5-21-other'))
    def test_cross_user_registration_and_removal_are_rejected_before_schtasks(self):
        with patch.object(runtime,'_schtasks') as tasks,patch.object(runtime,'_query_task') as query:
            for operation in (runtime.install_autostart,runtime.remove_autostart):
                with self.assertRaises(ValueError):operation(user_sid='S-1-5-21-other')
            tasks.assert_not_called()
            query.assert_not_called()
    def test_foreign_or_elevated_task_is_not_overwritten_or_deleted(self):
        for xml in [runtime._task_xml(self.prefix,'S-1-5-21-other'),runtime._task_xml(self.prefix,self.api.me['sid']).replace('LeastPrivilege','HighestAvailable')]:
            with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0,stdout=xml)),patch.object(runtime,'_schtasks') as tasks:
                with self.assertRaises(ValueError):runtime.install_autostart()
                with self.assertRaises(ValueError):runtime.remove_autostart()
                tasks.assert_not_called()
    def test_task_install_verifies_export_and_removal_does_not_stop_runtime(self):
        saved=[None];calls=[]
        def tasks(args):
            calls.append(args)
            if args[0]=='/Query':return SimpleNamespace(returncode=0 if saved[0] else 0x80070002,stdout=saved[0] or '')
            if args[0]=='/Create':saved[0]=Path(args[args.index('/XML')+1]).read_text(encoding='utf-16')
            if args[0]=='/Delete':saved[0]=None
            return SimpleNamespace(returncode=0,stdout='')
        with patch.object(runtime,'_schtasks',side_effect=tasks),patch.object(runtime,'_query_task',side_effect=lambda name:tasks(['/Query',name])),patch.object(runtime,'stop') as stop:
            self.assertTrue(runtime.install_autostart()['installed']);self.assertNotIn('/F',calls[1])
            self.assertTrue(runtime.remove_autostart()['removed']);stop.assert_not_called()
        self.assertFalse(list(self.directory.glob('autostart-*.xml')))
    def test_authentication_proof_binds_request_and_response(self):
        token='01'*32
        self.assertNotEqual(runtime._proof(token,b'{"op":"status"}'),runtime._proof(token,b'{"op":"stop"}'))
        self.assertNotIn(token,runtime._proof(token,b'request'))
    def test_authenticated_loopback_roundtrip_and_invalid_proof_rejection(self):
        class Sup:
            def __init__(self):self.stopping=threading.Event();self.closed=False
            def snapshot(self):return {'supported':True,'running':True,'state':'running','install':runtime._install_id(),'children':{}}
            def step(self):pass
            def close(self):self.closed=True
        sup=Sup();errors=[]
        def serve():
            try:runtime.run()
            except BaseException as exc:errors.append(exc)
        with patch.object(runtime,'Supervisor',return_value=sup):
            thread=threading.Thread(target=serve,daemon=True);thread.start()
            try:
                deadline=time.monotonic()+3
                while not runtime._record_path().exists() and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(runtime.status()['state'],'running')
                record=runtime._read_record()
                request=urllib.request.Request(f'http://127.0.0.1:{record["port"]}/control',data=b'{"op":"stop","nonce":"00000000000000000000000000000000"}',headers={'X-Snowball-Proof':'00'*32})
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with self.assertRaises(urllib.error.HTTPError) as response:opener.open(request,timeout=1)
                self.assertEqual(response.exception.code,403);self.assertFalse(sup.stopping.is_set())
                # An unauthenticated client cannot indefinitely block the only
                # control loop by declaring a body and never sending it.
                with socket.create_connection(('127.0.0.1',record['port']),timeout=1) as slow:
                    slow.sendall(b'POST /control HTTP/1.1\r\nContent-Length: 100\r\n\r\n')
                    time.sleep(.65)
                self.assertTrue(runtime._rpc(record,'status')['running'])
                runtime._rpc(record,'stop');thread.join(3)
                self.assertFalse(thread.is_alive());self.assertTrue(sup.closed)
                self.assertFalse(runtime._record_path().exists());self.assertEqual(errors,[])
            finally:
                sup.stopping.set();thread.join(3)
    @unittest.skipIf(os.name=='nt','Linux systemd compatibility test')
    def test_linux_service_definition_and_ownership_are_preserved(self):
        import user_services
        with patch.object(user_services.sys,'platform','linux'),patch.object(user_services,'UNITS',Path(self.temp.name)/'units'):
            user_services.UNITS.mkdir();path=user_services.UNITS/user_services.unit_name('bridge')
            path.write_text(user_services.contents('bridge','/fixture/python'))
            self.assertTrue(user_services.owned('bridge'))
            self.assertIn('Restart=on-failure',path.read_text())
            with patch.object(user_services.subprocess,'run') as runner:user_services.control('bridge','stop')
            self.assertEqual(runner.call_args.args[0],['systemctl','--user','stop','snowball-bridge.service'])
    @unittest.skipUnless(os.name=='nt','Real Win32 identity requires Windows')
    def test_win32_current_identity_and_empty_job(self):
        api=runtime.WinAPI();identity=api.identity(os.getpid())
        self.assertEqual(identity['pid'],os.getpid());self.assertGreaterEqual(identity['session'],0)
        if identity['session']==0:
            with self.assertRaises(RuntimeError):api.current()
        else:self.assertEqual(api.current(),identity)
        self.assertIsNone(api.identity(-1))
        self.assertIsInstance(api.host_job_context()['in_job'],bool)
        account=os.environ.get('USERDOMAIN','')+'\\'+os.environ.get('USERNAME','')
        self.assertEqual(api.account_sid(account),identity['sid'])
        job=api.job();api.close_job(job)
    def test_frozen_launch_prefers_console_dispatcher_and_preserves_explicit_prefix(self):
        gui=Path(self.temp.name)/'Snowball.exe';cli=gui.with_name('SnowballCLI.exe');gui.touch();cli.touch()
        with patch.object(runtime.sys,'executable',str(gui)),patch.object(runtime.sys,'frozen',True,create=True):
            self.assertEqual(ORIGINAL_LAUNCH_PREFIX(),[str(cli),'runtime'])
            self.assertEqual(ORIGINAL_LAUNCH_PREFIX(executable=gui,entry=['trusted','runtime']),[str(gui),'trusted','runtime'])
    def test_autostart_status_distinguishes_owned_foreign_and_unknown_without_writes(self):
        sid=self.api.me['sid']
        for xml,expected in [(runtime._task_xml(self.prefix,sid),'owned'),(runtime._task_xml(['old.exe','runtime'],sid),'other')]:
            with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0,stdout=xml)) as call,patch.object(runtime,'_schtasks') as write:
                self.assertEqual(runtime.autostart_status()['state'],expected)
                self.assertEqual(call.call_args.args[0],runtime._task_name(sid));write.assert_not_called()
        with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=1,stdout='')):
            self.assertIsNone(runtime.autostart_status()['installed']);self.assertEqual(runtime.autostart_status()['state'],'unavailable')
        with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0x80070002,stdout='')):
            self.assertEqual(runtime.autostart_status()['state'],'absent')
    def test_new_install_refuses_old_runtime_and_old_task(self):
        self.record(install='old-installation-id')
        with patch.object(runtime,'_rpc') as rpc:
            self.assertEqual(runtime.status()['state'],'unverified')
            with self.assertRaises(ValueError):runtime.stop()
            rpc.assert_not_called()
        old=runtime._task_xml(['old.exe','runtime'],self.api.me['sid'])
        with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0,stdout=old)) as call:
            with self.assertRaises(ValueError):runtime.install_autostart()
            self.assertEqual(call.call_count,1)
    def test_task_xml_transport_decoding_preserves_non_ascii_paths(self):
        text='<?xml version="1.0" encoding="UTF-16"?><Task>雪球</Task>'
        for raw,encoding in [(text.encode('utf-16'),'cp936'),(text.encode('utf-8'),'cp936'),(text.encode('gbk'),'cp936')]:
            self.assertEqual(runtime._decode_task_output(raw,encoding),text)
            self.assertEqual(ET.fromstring(runtime._decode_task_output(raw,encoding)).text,'雪球')

    def task_accounts(self,principal,trigger):
        root=ET.fromstring(runtime._task_xml(self.prefix,self.api.me['sid']))
        ns={'t':runtime.TASK_NS}
        root.find('t:Principals/t:Principal/t:UserId',ns).text=principal
        root.find('t:Triggers/t:LogonTrigger/t:UserId',ns).text=trigger
        return ET.tostring(root,encoding='unicode')

    def test_normalized_task_account_names_require_same_resolved_sid(self):
        sid=self.api.me['sid'];alias='FIXTURE\\雪用户'
        with patch.object(self.api,'account_sid',return_value=sid) as lookup:
            for principal,trigger in ((alias,sid),(sid,alias),(alias,alias)):
                self.assertTrue(runtime._owned_task(self.task_accounts(principal,trigger),self.prefix,sid))
            self.assertTrue(all(call.args==(alias,) for call in lookup.call_args_list))
        for value in ('S-1-5-21-999',''):
            with patch.object(self.api,'account_sid') as lookup:
                self.assertFalse(runtime._owned_task(self.task_accounts(value,sid),self.prefix,sid))
                lookup.assert_not_called()
        with patch.object(self.api,'account_sid',return_value='S-1-5-21-999'):
            self.assertFalse(runtime._owned_task(self.task_accounts(alias,sid),self.prefix,sid))
        with patch.object(self.api,'account_sid',side_effect=OSError('unresolved private name')):
            self.assertFalse(runtime._owned_task(self.task_accounts(sid,alias),self.prefix,sid))

    def test_account_normalization_does_not_weaken_marker_executable_arguments_or_privilege(self):
        sid=self.api.me['sid'];xml=self.task_accounts('FIXTURE\\user','FIXTURE\\user')
        with patch.object(self.api,'account_sid',return_value=sid):
            for before,after in ((self.prefix[0],'foreign.exe'),('runtime run','runtime stop'),
                                 ('LeastPrivilege','HighestAvailable'),('InteractiveToken','Password'),
                                 (runtime._task_marker(sid),'unowned task')):
                self.assertFalse(runtime._owned_task(xml.replace(before,after),self.prefix,sid))

    def test_utf16_task_transport_accepts_bomless_newline_prefix_and_preserves_owned_values(self):
        sid=self.api.me['sid'];xml='<?xml version="1.0" encoding="UTF-16"?>'+runtime._task_xml(self.prefix,sid)
        for encoding in ('utf-16-le','utf-16-be','utf-16','utf-8-sig'):
            for prefix in ('','\r\n',' \t\n'):
                decoded=runtime._decode_task_output((prefix+xml).encode(encoding),'cp936')
                self.assertEqual(decoded,xml)
                self.assertTrue(runtime._owned_task(decoded,self.prefix,sid))
        with self.assertRaises(UnicodeDecodeError):
            runtime._decode_task_output(b'<\x00x','cp936')

    def test_task_create_verify_and_remove_accept_windows_normalized_user_names(self):
        saved=[None];sid=self.api.me['sid'];calls=[]
        def tasks(args):
            calls.append(args[0])
            if args[0]=='/Query':return SimpleNamespace(returncode=0 if saved[0] else 0x80070002,stdout=saved[0] or '')
            if args[0]=='/Create':saved[0]=self.task_accounts('FIXTURE\\user','FIXTURE\\user')
            if args[0]=='/Delete':saved[0]=None
            return SimpleNamespace(returncode=0,stdout='')
        with patch.object(self.api,'account_sid',return_value=sid),patch.object(runtime,'_schtasks',side_effect=tasks),patch.object(runtime,'_query_task',side_effect=lambda name:tasks(['/Query',name])):
            self.assertTrue(runtime.install_autostart()['installed'])
            self.assertEqual(runtime.autostart_status()['state'],'owned')
            self.assertTrue(runtime.remove_autostart()['removed'])
            self.assertEqual(runtime.autostart_status()['state'],'absent')
        self.assertEqual(calls.count('/Create'),1);self.assertEqual(calls.count('/Delete'),1)

    def test_task_check_report_only_exposes_fixed_boolean_fields(self):
        checks=runtime._task_ownership_checks(self.task_accounts('UNKNOWN\\private',self.api.me['sid']),self.prefix,self.api.me['sid'])
        self.assertFalse(checks['principal_user']);self.assertTrue(checks['trigger_user'])
        self.assertTrue(all(type(v) is bool for v in checks.values()))
        checks['private']='PRIVATE_SENTINEL';checks['command']='PRIVATE_SENTINEL'
        report=runtime.error_report(runtime.RuntimeFailure('task_create_unverified',details={'task_checks':checks}))
        self.assertNotIn('PRIVATE_SENTINEL',json.dumps(report));self.assertNotIn('private',report['task_checks'])
        self.assertFalse(any(runtime._task_ownership_checks('bad XML',self.prefix,self.api.me['sid']).values()))

    def test_native_account_sid_uses_bounded_two_pass_user_lookup_and_frees_sid_string(self):
        last_error=[0];kind=[1];sid_size=[28];second_ok=[True]
        class CTypes:
            def __getattr__(self,name):return getattr(ctypes,name)
            @staticmethod
            def set_last_error(value):last_error[0]=value
            @staticmethod
            def get_last_error():return last_error[0]
            @staticmethod
            def WinError(code):
                error=OSError('private native detail');error.winerror=code;return error
        api=object.__new__(runtime.WinAPI);api.c=CTypes()
        api.w=SimpleNamespace(DWORD=ctypes.c_uint32,LPWSTR=ctypes.c_wchar_p)
        api.a=Mock();api.k=Mock();calls=[]
        def lookup(system,name,sid,size,domain,domain_size,use):
            self.assertIsNone(system);self.assertEqual(name,'FIXTURE\\user');calls.append(sid is None)
            if sid is None:
                size._obj.value=sid_size[0];domain_size._obj.value=8;last_error[0]=122;return False
            use._obj.value=kind[0];last_error[0]=1332;return second_ok[0]
        api.a.LookupAccountNameW.side_effect=lookup;api.a.IsValidSid.return_value=True
        def convert(sid,out):out._obj.value=self.api.me['sid'];return True
        api.a.ConvertSidToStringSidW.side_effect=convert
        self.assertEqual(api.account_sid('FIXTURE\\user'),self.api.me['sid'])
        self.assertEqual(calls,[True,False]);api.k.LocalFree.assert_called_once()
        api.a.ConvertSidToStringSidW.reset_mock();kind[0]=2
        with self.assertRaises(ValueError):api.account_sid('FIXTURE\\user')
        api.a.ConvertSidToStringSidW.assert_not_called()
        calls.clear();sid_size[0]=69
        with self.assertRaises(OSError):api.account_sid('FIXTURE\\user')
        self.assertEqual(calls,[True])
        sid_size[0]=28;second_ok[0]=False
        with self.assertRaises(OSError) as raised:api.account_sid('FIXTURE\\user')
        self.assertEqual(raised.exception.winerror,1332)
        api.a.LookupAccountNameW.reset_mock()
        for name in ('', 'user\0other', 'x'*513):
            with self.assertRaises(ValueError):api.account_sid(name)
        api.a.LookupAccountNameW.assert_not_called()
    def test_task_access_denied_is_not_absent_and_does_not_create_or_delete(self):
        with patch.object(runtime,'_query_task',return_value=SimpleNamespace(returncode=0x80070005,stdout='')),patch.object(runtime,'_schtasks') as write:
            self.assertEqual(runtime.autostart_status()['state'],'unavailable')
            with self.assertRaises(RuntimeError):runtime.install_autostart()
            with self.assertRaises(RuntimeError):runtime.remove_autostart()
            write.assert_not_called()

    def test_only_missing_run_level_uses_scheduler_least_privilege_default(self):
        sid=self.api.me['sid'];root=ET.fromstring(runtime._task_xml(self.prefix,sid))
        principal=root.find('{'+runtime.TASK_NS+'}Principals/{'+runtime.TASK_NS+'}Principal')
        level=principal.find('{'+runtime.TASK_NS+'}RunLevel');principal.remove(level)
        self.assertTrue(runtime._owned_task(ET.tostring(root,encoding='unicode'),self.prefix,sid))
        principal.append(level)
        for value in ('',' ',None,'HighestAvailable','Unknown','leastprivilege'):
            level.text=value
            self.assertFalse(runtime._owned_task(ET.tostring(root,encoding='unicode'),self.prefix,sid))
        level.text='LeastPrivilege';principal.append(ET.fromstring(ET.tostring(level)))
        self.assertFalse(runtime._owned_task(ET.tostring(root,encoding='unicode'),self.prefix,sid))

    def test_com_query_preserves_unicode_task_paths_and_uses_fixed_script_and_environment(self):
        sid=self.api.me['sid'];name=runtime._task_name(sid)
        base=Path(self.temp.name)/'合成 用户 profile'/'应用 数据'/'本地'
        prefix=[str(base/'SnowballCLI.exe'),'runtime']
        with patch.object(runtime,'BASE',base):
            xml=runtime._task_xml(prefix,sid).replace('<RunLevel>LeastPrivilege</RunLevel>','')
            raw=json.dumps({'v':1,'ok':True,'xml':xml},ensure_ascii=False).encode('utf-8')
            native=SimpleNamespace(returncode=0,stdout=raw,stderr=b'PRIVATE_SENTINEL')
            with patch.object(runtime.subprocess,'run',return_value=native) as run:
                result=runtime._query_task(name)
            self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,xml);self.assertEqual(result.stderr,'')
            self.assertTrue(runtime._owned_task(result.stdout,prefix,sid))
            self.assertFalse(runtime._owned_task(result.stdout.replace('合成 用户','?? ??'),prefix,sid))
        argv=run.call_args.args[0]
        self.assertEqual(Path(argv[0]).name,'powershell.exe')
        self.assertEqual(base64.b64decode(argv[-1]).decode('utf-16-le'),runtime.TASK_QUERY_SCRIPT)
        self.assertNotIn(name,runtime.TASK_QUERY_SCRIPT)
        self.assertEqual(run.call_args.kwargs['env']['SNOWBALL_TASK_NAME'],name)
        self.assertNotIn('chcp',runtime.TASK_QUERY_SCRIPT.lower())
        self.assertIn('[Console]::OutputEncoding',runtime.TASK_QUERY_SCRIPT)
        self.assertIn('$task.Xml',runtime.TASK_QUERY_SCRIPT)

    def test_com_query_preserves_hresult_and_never_treats_permission_or_invalid_output_as_missing(self):
        name=runtime._task_name(self.api.me['sid'])
        for code,absent in ((0x80070002,True),(0x80070003,True),(0x80070005,False),(0x80041315,False)):
            raw=json.dumps({'v':1,'ok':False,'hresult':code}).encode()
            with patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=raw,stderr=b'private')):
                result=runtime._query_task(name)
            self.assertEqual(result.returncode,code);self.assertEqual(runtime._task_absent(result),absent)
            self.assertEqual(result.stdout,'');self.assertEqual(result.stderr,'')
        for raw in (b'private raw error',b'\xff',b'x'*262145,
                    b'{"v":1,"ok":false,"hresult":0}',b'{"v":1,"ok":false,"hresult":-1}',
                    b'{"v":1,"ok":false,"hresult":true}',b'{"v":1,"ok":true,"xml":""}'):
            with patch.object(runtime.subprocess,'run',return_value=SimpleNamespace(returncode=1,stdout=raw,stderr=b'private')):
                result=runtime._query_task(name)
            self.assertNotEqual(result.returncode,0);self.assertFalse(runtime._task_absent(result))
            self.assertEqual(result.stdout,'');self.assertEqual(result.stderr,'')
        with patch.object(runtime.subprocess,'run') as run:
            for invalid in ('OtherTask','SnowballCompanion-'+('a'*16)+';exit','../task',None):
                with self.assertRaises(ValueError):runtime._query_task(invalid)
            run.assert_not_called()

    def test_com_query_transport_timeout_has_fixed_diagnostic_without_command_leak(self):
        error=subprocess.TimeoutExpired('PRIVATE_COMMAND_SENTINEL',20)
        with patch.object(runtime.subprocess,'run',side_effect=error):
            with self.assertRaises(runtime.RuntimeFailure) as raised:
                runtime._query_task(runtime._task_name(self.api.me['sid']))
        report=runtime.error_report(raised.exception)
        self.assertEqual(report['code'],'task_query_failed')
        self.assertNotIn('PRIVATE_COMMAND_SENTINEL',json.dumps(report))
    def test_start_uses_hidden_breakaway_process_and_timeout_only_owns_its_handle(self):
        process=Process(123);outsider=Process(999)
        probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
        with patch.object(runtime,'status',side_effect=[{'running':False,'state':'stopped'},{'running':True,'state':'running'}]),patch.object(runtime.socket,'socket',return_value=probe),patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()),patch.object(runtime.subprocess,'Popen',return_value=process) as spawn:
            self.assertTrue(runtime.start()['running']);flags=spawn.call_args.kwargs['creationflags']
            self.assertTrue(flags&0x01000000);self.assertTrue(flags&0x08000000)
        with patch.object(runtime,'status',return_value={'running':False,'state':'stopped'}),patch.object(runtime.socket,'socket',return_value=probe),patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()),patch.object(runtime.subprocess,'Popen',return_value=process):
            with self.assertRaises(RuntimeError):runtime.start(timeout=0)
        self.assertEqual(process.terminated,1);self.assertEqual(outsider.terminated,0)
    def test_windows_service_ownership_uses_verified_task_not_platform_presence(self):
        import user_services
        with patch.object(user_services.sys,'platform','win32'):
            for value in (False,True):
                with patch.object(runtime,'autostart_status',return_value={'owned':value}):
                    self.assertEqual(user_services.owned('hub'),value)
    def test_non_windows_status_has_no_process_or_task_side_effect(self):
        with patch.object(runtime,'_windows',return_value=False),patch.object(runtime,'_read_record') as read:
            self.assertFalse(runtime.status()['supported']);read.assert_not_called()

    def test_safe_error_report_never_emits_exception_text_paths_or_commands(self):
        secret='PRIVATE_PATH_AND_KEY_SENTINEL'
        error=PermissionError(13,secret,secret);error.winerror=5
        controlled=runtime.RuntimeFailure('supervisor_spawn_failed',cause=error,
            details={'in_job':False,'exitcode':7,'path':secret,'command':secret,'token':secret})
        report=runtime.error_report(controlled)
        self.assertEqual(report['code'],'supervisor_spawn_failed')
        self.assertEqual(report['error_type'],'PermissionError')
        self.assertEqual(report['winerror'],5);self.assertEqual(report['errno'],13)
        self.assertFalse(report['in_job']);self.assertNotIn(secret,json.dumps(report))
        for other in (RuntimeError(secret),ValueError(secret),
                      subprocess.TimeoutExpired(secret,20,output=secret,stderr=secret)):
            self.assertNotIn(secret,json.dumps(runtime.error_report(other)))
        report=runtime.error_report(runtime.RuntimeFailure(secret,details={'in_job':secret,'exitcode':secret}))
        self.assertEqual(report['code'],'runtime_failed');self.assertNotIn(secret,json.dumps(report))

    def test_cli_emits_fixed_code_and_numeric_winerror_without_raw_exception(self):
        error=PermissionError(13,'PRIVATE_SENTINEL');error.winerror=5
        failure=runtime.RuntimeFailure('supervisor_spawn_failed',cause=error)
        output=io.StringIO()
        with patch.object(runtime,'start',side_effect=failure),redirect_stderr(output):
            self.assertEqual(runtime.main(['start']),2)
        report=json.loads(output.getvalue())
        self.assertEqual(report['operation'],'start')
        self.assertEqual(report['code'],'supervisor_spawn_failed')
        self.assertEqual(report['winerror'],5);self.assertEqual(report['error_type'],'PermissionError')
        self.assertNotIn('PRIVATE_SENTINEL',output.getvalue())

    def test_session_zero_has_actionable_code_not_inferred_job_restriction(self):
        api=object.__new__(runtime.WinAPI)
        with patch.object(api,'identity',return_value=dict(self.api.me,session=0)):
            with self.assertRaises(runtime.RuntimeFailure) as raised:api.current()
        self.assertEqual(runtime.error_report(raised.exception)['code'],'interactive_session_required')
        with patch.object(api,'identity',return_value=None):
            with self.assertRaises(runtime.RuntimeFailure) as raised:api.current()
        self.assertEqual(raised.exception.code,'process_identity_unavailable')

    def test_spawn_access_denied_requires_confirmed_job_flags_and_never_downgrades(self):
        probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
        error=PermissionError(13,'PRIVATE_SENTINEL');error.winerror=5
        cases=[({'in_job':True,'breakaway_allowed':False,'silent_breakaway':False},'host_job_restricted'),
               ({'in_job':False},'supervisor_spawn_failed'),
               ({'in_job':True,'breakaway_allowed':True,'silent_breakaway':False},'supervisor_spawn_failed'),
               ({'in_job':True,'breakaway_allowed':False,'silent_breakaway':True},'supervisor_spawn_failed'),
               ({'in_job':True},'supervisor_spawn_failed')]
        for context,expected in cases:
            with self.subTest(context=context), \
                 patch.object(runtime,'status',return_value={'state':'stopped','running':False}), \
                 patch.object(runtime.socket,'socket',return_value=probe), \
                 patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()), \
                 patch.object(self.api,'host_job_context',return_value=context), \
                 patch.object(runtime.subprocess,'Popen',side_effect=error) as spawn:
                with self.assertRaises(runtime.RuntimeFailure) as raised:runtime.start()
                self.assertEqual(raised.exception.code,expected)
                self.assertEqual(spawn.call_count,1)
                self.assertTrue(spawn.call_args.kwargs['creationflags']&0x01000000)
                self.assertEqual(runtime.error_report(raised.exception)['winerror'],5)

    def test_failed_job_query_keeps_spawn_error_and_non_access_denied_does_not_query(self):
        probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
        for code in (5,2):
            error=OSError(22,'PRIVATE_SENTINEL');error.winerror=code
            with patch.object(runtime,'status',return_value={'state':'stopped','running':False}), \
                 patch.object(runtime.socket,'socket',return_value=probe), \
                 patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()), \
                 patch.object(self.api,'host_job_context',side_effect=OSError('private query failure')) as query, \
                 patch.object(runtime.subprocess,'Popen',side_effect=error):
                with self.assertRaises(runtime.RuntimeFailure) as raised:runtime.start()
                self.assertEqual(raised.exception.code,'supervisor_spawn_failed')
                self.assertEqual(runtime.error_report(raised.exception)['winerror'],code)
                self.assertEqual(query.call_count,1 if code==5 else 0)

    def test_native_job_context_decodes_real_limit_bits_without_mutating_job(self):
        api=object.__new__(runtime.WinAPI);api.c=ctypes
        api.w=SimpleNamespace(DWORD=ctypes.c_uint32,BOOL=ctypes.c_int)
        api.k=Mock();api.k.GetCurrentProcess.return_value=-1
        inside=[True];flags=[0]
        def member(process,job,result):result._obj.value=inside[0];return True
        def query(job,kind,out,size,actual):
            self.assertIsNone(job);self.assertEqual(kind,2)
            out._obj.flags=flags[0];return True
        api.k.IsProcessInJob.side_effect=member
        api.k.QueryInformationJobObject.side_effect=query
        for value,expected in [(0,(False,False)),(0x800,(True,False)),(0x1000,(False,True)),(0x1800,(True,True))]:
            flags[0]=value;context=api.host_job_context()
            self.assertTrue(context['in_job'])
            self.assertEqual((context['breakaway_allowed'],context['silent_breakaway']),expected)
        inside[0]=False;api.k.QueryInformationJobObject.reset_mock()
        self.assertEqual(api.host_job_context(),{'in_job':False})
        api.k.QueryInformationJobObject.assert_not_called()
        api.k.SetInformationJobObject.assert_not_called()

    def test_controlled_child_assignment_failure_is_reported_and_backs_off(self):
        supervisor=self.supervisor();output=io.StringIO()
        with patch.object(self.api,'assign',side_effect=runtime.RuntimeFailure('child_job_assignment_failed')),redirect_stderr(output):
            supervisor.step()
        self.assertEqual(supervisor.children['hub']['state'],'backoff')
        self.assertEqual(self.processes[0].terminated,1)
        self.assertEqual(json.loads(output.getvalue())['code'],'child_job_assignment_failed')
        supervisor.close()

    def test_early_supervisor_exit_preserves_numeric_exitcode(self):
        process=Process(123);process.returncode=17
        probe=Mock();probe.__enter__=Mock(return_value=probe);probe.__exit__=Mock(return_value=False)
        with patch.object(runtime,'status',return_value={'state':'stopped','running':False}), \
             patch.object(runtime.socket,'socket',return_value=probe), \
             patch.object(runtime,'_log_stream',side_effect=lambda _:io.BytesIO()), \
             patch.object(runtime.subprocess,'Popen',return_value=process):
            with self.assertRaises(runtime.RuntimeFailure) as raised:runtime.start()
        self.assertEqual(raised.exception.code,'supervisor_exited')
        self.assertEqual(runtime.error_report(raised.exception)['exitcode'],17)
        self.assertEqual(process.terminated,0)

if __name__=='__main__':unittest.main()
