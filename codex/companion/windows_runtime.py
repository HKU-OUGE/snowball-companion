"""Current-user interactive Windows supervisor for the shared Hub and BLE bridge.

Entry contract: ordinary Python uses [python[ w ].exe, absolute __file__]; a
frozen dispatcher uses [SnowballCLI.exe, 'runtime']. APIs accept executable=
and entry= to select that fixed installation prefix. The dispatcher must call
main() with the remaining arguments. No Session 0 service or device credential
migration is performed. Requires Windows 10+ for nested Job Objects.

Microsoft contracts used: taskschd/security-contexts-for-running-tasks,
procthread/job-objects, processthreadsapi/GetProcessTimes and
winbase/QueryFullProcessImageNameW (learn.microsoft.com/windows/win32).
"""
from contextlib import contextmanager
import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET

BASE = Path(__file__).resolve().parent
STATE = Path.home()/'.local/state/xueqiu'
DIRECTORY = STATE/'windows-runtime'
TASK_NS = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
API_VERSION = 1
MAX_CONTROL = 8192
_api_instance = None


ERROR_CODES = frozenset({
    'windows_required', 'process_identity_unavailable', 'interactive_session_required',
    'runtime_unreachable', 'runtime_already_owned', 'hub_port_unavailable',
    'host_job_restricted', 'supervisor_spawn_failed', 'supervisor_exited',
    'supervisor_start_timeout', 'runtime_identity_changed', 'supervisor_stop_timeout',
    'task_query_failed', 'task_create_failed', 'task_create_unverified',
    'task_delete_failed', 'task_delete_unverified', 'child_job_create_failed',
    'child_job_limits_failed', 'child_job_assignment_failed', 'runtime_failed',
})
TASK_CHECKS = ('single_principal','single_action','single_trigger','marker',
               'principal_user','logon_type','run_level','trigger_user',
               'command','arguments','working_directory')


class RuntimeFailure(RuntimeError):
    """Fixed diagnostic codes, never OS messages, command lines or user paths."""
    def __init__(self, code, *, cause=None, details=None):
        self.code = code if code in ERROR_CODES else 'runtime_failed'
        self.cause = cause
        self.details = details or {}
        super().__init__(self.code)


def error_report(error):
    """Public CLI/GUI diagnostic; all displayed text comes from fixed literals."""
    controlled = isinstance(error, RuntimeFailure)
    cause = error.cause if controlled and error.cause is not None else error
    # Whitelist class labels too: arbitrary exception names are not diagnostics.
    types = (subprocess.TimeoutExpired, subprocess.CalledProcessError,
             PermissionError, FileNotFoundError, BlockingIOError, OSError,
             ValueError, RuntimeError)
    kind = next((t.__name__ for t in types if isinstance(cause, t)), 'RuntimeError')
    code = error.code if controlled else ('os_error' if isinstance(cause, OSError) else 'runtime_failed')
    messages = {
        'windows_required': '后台需要在 Windows 中运行。',
        'process_identity_unavailable': '无法验证当前 Windows 进程身份，未启动后台。',
        'interactive_session_required': '请登录 Windows 后，从开始菜单打开雪球；后台不能运行在系统服务会话中。',
        'runtime_unreachable': '已安装后台暂未响应，请查看连接诊断后重试。',
        'runtime_already_owned': '当前用户已有雪球后台运行，请先在原版本中停止后台。',
        'hub_port_unavailable': '本机同步端口被占用，未关闭已有程序。请查看连接诊断。',
        'host_job_restricted': '当前启动环境不允许后台独立运行。请从开始菜单或资源管理器打开雪球，不要从受限终端启动。',
        'supervisor_spawn_failed': 'Windows 未能启动独立后台，请查看诊断中的系统错误码。',
        'supervisor_exited': '后台启动后提前退出，请查看后台诊断日志。',
        'supervisor_start_timeout': '后台启动未得到确认，已停止本次创建的进程。',
        'runtime_identity_changed': '后台进程身份已变化，未停止其他进程。请查看连接诊断。',
        'supervisor_stop_timeout': '后台停止尚未得到确认，请稍后查看状态；没有强制关闭其他程序。',
        'task_query_failed': '无法确认登录启动项状态，请检查 Windows 任务计划程序。',
        'task_create_failed': '登录启动项创建失败，请检查 Windows 任务计划程序。',
        'task_create_unverified': '登录启动项可能已创建，但尚未核实，请查看后台设置。',
        'task_delete_failed': '登录启动项删除失败，请检查 Windows 任务计划程序。',
        'task_delete_unverified': '登录启动项可能已删除，但尚未核实，请查看后台设置。',
        'child_job_create_failed': '无法创建后台子进程管理组，后台未启动。',
        'child_job_limits_failed': '无法设置后台子进程退出保护，后台未启动。',
        'child_job_assignment_failed': '无法安全管理后台子进程，该子进程已停止。',
    }
    result = {'code': code, 'error_type': kind,
              'message': messages.get(code, '后台操作未完成，请查看连接诊断和系统错误码。')}
    for name in ('winerror', 'errno'):
        value = getattr(cause, name, None)
        if type(value) is int: result[name] = value
    if controlled:
        for name in ('in_job', 'breakaway_allowed', 'silent_breakaway'):
            if type(error.details.get(name)) is bool: result[name] = error.details[name]
        if type(error.details.get('exitcode')) is int: result['exitcode'] = error.details['exitcode']
        checks=error.details.get('task_checks')
        if isinstance(checks,dict):
            result['task_checks']={name:checks[name] for name in TASK_CHECKS if type(checks.get(name)) is bool}
    return result


def _error_record(error, operation):
    return {'ok': False, 'error': 'Windows runtime operation failed; inspect private runtime logs',
            'operation': operation, **error_report(error)}


def _windows():
    return sys.platform == 'win32'


def _require_windows():
    if not _windows():
        raise RuntimeFailure('windows_required')


def _install_id():
    return hashlib.sha256(os.path.normcase(str(BASE)).encode('utf-8')).hexdigest()[:24]


def launch_prefix(executable=None, entry=None):
    """No shell. entry is a fixed argv prefix, not an interpolated command."""
    if executable is None:
        cli = Path(sys.executable).with_name('SnowballCLI.exe')
        executable = cli if getattr(sys, 'frozen', False) and cli.is_file() else sys.executable
    executable = str(Path(executable).absolute())
    if entry is None:
        entry = ['runtime'] if getattr(sys, 'frozen', False) else [str(Path(__file__).resolve())]
    if isinstance(entry, (str, Path)):
        entry = [str(entry)]
    if not entry or any(not isinstance(s, str) or '\0' in s for s in entry):
        raise ValueError('A fixed runtime entry argv is required')
    if not Path(executable).is_file():
        raise ValueError('Runtime executable is missing')
    return [executable, *entry]


class WinAPI:
    """Stable process handles/creation identity; no /proc, taskkill or PID kill."""
    def __init__(self):
        _require_windows()
        import ctypes as c
        from ctypes import wintypes as w
        self.c, self.w = c, w
        self.k = c.WinDLL('kernel32', use_last_error=True)
        self.a = c.WinDLL('advapi32', use_last_error=True)
        def bind(dll, name, result, *args):
            fn = getattr(dll, name); fn.restype = result; fn.argtypes = list(args)
        bind(self.k,'OpenProcess',w.HANDLE,w.DWORD,w.BOOL,w.DWORD)
        bind(self.k,'CloseHandle',w.BOOL,w.HANDLE)
        bind(self.k,'GetProcessTimes',w.BOOL,w.HANDLE,*([c.POINTER(w.FILETIME)]*4))
        bind(self.k,'GetExitCodeProcess',w.BOOL,w.HANDLE,c.POINTER(w.DWORD))
        bind(self.k,'WaitForSingleObject',w.DWORD,w.HANDLE,w.DWORD)
        bind(self.k,'QueryFullProcessImageNameW',w.BOOL,w.HANDLE,w.DWORD,w.LPWSTR,c.POINTER(w.DWORD))
        bind(self.k,'ProcessIdToSessionId',w.BOOL,w.DWORD,c.POINTER(w.DWORD))
        bind(self.a,'OpenProcessToken',w.BOOL,w.HANDLE,w.DWORD,c.POINTER(w.HANDLE))
        bind(self.a,'GetTokenInformation',w.BOOL,w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD))
        bind(self.a,'ConvertSidToStringSidW',w.BOOL,c.c_void_p,c.POINTER(w.LPWSTR))
        bind(self.a,'LookupAccountNameW',w.BOOL,w.LPCWSTR,w.LPCWSTR,c.c_void_p,
             c.POINTER(w.DWORD),w.LPWSTR,c.POINTER(w.DWORD),c.POINTER(c.c_int))
        bind(self.a,'IsValidSid',w.BOOL,c.c_void_p)
        bind(self.k,'LocalFree',c.c_void_p,c.c_void_p)
        bind(self.k,'CreateJobObjectW',w.HANDLE,c.c_void_p,w.LPCWSTR)
        bind(self.k,'SetInformationJobObject',w.BOOL,w.HANDLE,c.c_int,c.c_void_p,w.DWORD)
        bind(self.k,'AssignProcessToJobObject',w.BOOL,w.HANDLE,w.HANDLE)
        bind(self.k,'TerminateJobObject',w.BOOL,w.HANDLE,w.UINT)
        bind(self.k,'GetStdHandle',w.HANDLE,w.DWORD)
        bind(self.k,'ReadFile',w.BOOL,w.HANDLE,c.c_void_p,w.DWORD,c.POINTER(w.DWORD),c.c_void_p)
        bind(self.k,'GetOEMCP',w.UINT)
        bind(self.k,'GetConsoleOutputCP',w.UINT)
        bind(self.k,'GetSystemDirectoryW',w.UINT,w.LPWSTR,w.UINT)
        bind(self.k,'GetCurrentProcess',w.HANDLE)
        bind(self.k,'IsProcessInJob',w.BOOL,w.HANDLE,w.HANDLE,c.POINTER(w.BOOL))
        bind(self.k,'QueryInformationJobObject',w.BOOL,w.HANDLE,c.c_int,c.c_void_p,w.DWORD,c.POINTER(w.DWORD))

    def task_tool(self):
        path=self.c.create_unicode_buffer(32768)
        count=self.k.GetSystemDirectoryW(path,len(path))
        if not 0<count<len(path):raise self.c.WinError(self.c.get_last_error())
        return str(Path(path.value)/'schtasks.exe')

    def task_encoding(self):
        return 'cp'+str(self.k.GetConsoleOutputCP() or self.k.GetOEMCP())

    def _sid(self, process):
        c,w=self.c,self.w;token=w.HANDLE()
        if not self.a.OpenProcessToken(process,8,c.byref(token)):raise c.WinError(c.get_last_error())
        try:
            size=w.DWORD();self.a.GetTokenInformation(token,1,None,0,c.byref(size))
            buf=c.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token,1,buf,size,c.byref(size)):raise c.WinError(c.get_last_error())
            pointer=c.cast(buf,c.POINTER(c.c_void_p))[0]; text=w.LPWSTR()
            if not self.a.ConvertSidToStringSidW(pointer,c.byref(text)):raise c.WinError(c.get_last_error())
            try:return text.value
            finally:self.k.LocalFree(c.cast(text,c.c_void_p))
        finally:self.k.CloseHandle(token)

    def identity(self,pid):
        if type(pid) is not int or pid<=0:return None
        c,w=self.c,self.w;handle=self.k.OpenProcess(0x1000|0x100000,False,pid)
        if not handle:return None
        try:
            code=w.DWORD();created,exited,kernel,user=(w.FILETIME() for _ in range(4))
            size=w.DWORD(32768);exe=c.create_unicode_buffer(size.value);session=w.DWORD()
            if (self.k.WaitForSingleObject(handle,0)!=0x102 or
                not self.k.GetExitCodeProcess(handle,c.byref(code)) or code.value!=259 or
                not self.k.GetProcessTimes(handle,c.byref(created),c.byref(exited),c.byref(kernel),c.byref(user)) or
                not self.k.QueryFullProcessImageNameW(handle,0,exe,c.byref(size)) or
                not self.k.ProcessIdToSessionId(pid,c.byref(session))):return None
            return {'pid':pid,'start':str((created.dwHighDateTime<<32)|created.dwLowDateTime),
                    'exe':os.path.normcase(exe.value),'sid':self._sid(handle),'session':session.value}
        except OSError:return None
        finally:self.k.CloseHandle(handle)

    def current(self):
        identity=self.identity(os.getpid())
        if not identity:raise RuntimeFailure('process_identity_unavailable')
        if identity['session']==0:raise RuntimeFailure('interactive_session_required')
        return identity

    def account_sid(self,name):
        """Resolve Scheduler's canonical account name, never compare a username alone."""
        if not isinstance(name,str) or not 1<=len(name)<=512 or '\0' in name:
            raise ValueError('Invalid task account identity')
        c,w=self.c,self.w;size=w.DWORD();domain_size=w.DWORD();kind=c.c_int()
        c.set_last_error(0)
        self.a.LookupAccountNameW(None,name,None,c.byref(size),None,c.byref(domain_size),c.byref(kind))
        error=c.get_last_error()
        # 122 is ERROR_INSUFFICIENT_BUFFER. Bound native allocations even for
        # malformed task names; group/service identities never authorize a task.
        if error!=122 or not 1<=size.value<=68 or not 1<=domain_size.value<=32768:
            raise c.WinError(error)
        sid=c.create_string_buffer(size.value);domain=c.create_unicode_buffer(domain_size.value)
        if not self.a.LookupAccountNameW(None,name,sid,c.byref(size),domain,c.byref(domain_size),c.byref(kind)):
            raise c.WinError(c.get_last_error())
        if kind.value!=1 or not self.a.IsValidSid(sid):
            raise ValueError('Task account is not an individual user')
        text=w.LPWSTR()
        if not self.a.ConvertSidToStringSidW(sid,c.byref(text)):
            raise c.WinError(c.get_last_error())
        try:return text.value
        finally:self.k.LocalFree(c.cast(text,c.c_void_p))

    def _job_basic_type(self):
        c,w=self.c,self.w
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
                      ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),
                      ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        return Basic

    def host_job_context(self):
        """Read the current immediate Job only; no policy change or process launch."""
        c,w=self.c,self.w;inside=w.BOOL()
        if not self.k.IsProcessInJob(self.k.GetCurrentProcess(),None,c.byref(inside)):
            raise c.WinError(c.get_last_error())
        result={'in_job':bool(inside.value)}
        if inside.value:
            limits=self._job_basic_type()()
            if not self.k.QueryInformationJobObject(None,2,c.byref(limits),c.sizeof(limits),None):
                raise c.WinError(c.get_last_error())
            result.update(breakaway_allowed=bool(limits.flags&0x800),
                          silent_breakaway=bool(limits.flags&0x1000))
        return result

    def job(self):
        c,w=self.c,self.w;Basic=self._job_basic_type()
        class IO(c.Structure):_fields_=[('values',c.c_uint64*6)]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_mem',c.c_size_t),('job_mem',c.c_size_t),
                      ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        handle=self.k.CreateJobObjectW(None,None)
        if not handle:raise RuntimeFailure('child_job_create_failed',cause=c.WinError(c.get_last_error()))
        limits=Extended();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(handle,9,c.byref(limits),c.sizeof(limits)):
            error=c.WinError(c.get_last_error())
            self.k.CloseHandle(handle);raise RuntimeFailure('child_job_limits_failed',cause=error)
        return handle

    def assign(self,job,child):
        if not self.k.AssignProcessToJobObject(job,int(child._handle)):
            raise RuntimeFailure('child_job_assignment_failed',cause=self.c.WinError(self.c.get_last_error()))

    def close_job(self,job):
        self.k.CloseHandle(job)  # Kernel terminates only processes assigned to this job.

    def gate(self):
        # pythonw can expose sys.stdin=None despite a valid inherited pipe.
        byte=self.c.create_string_buffer(1);count=self.w.DWORD()
        handle=self.k.GetStdHandle(0xfffffff6)
        return bool(self.k.ReadFile(handle,byte,1,self.c.byref(count),None) and count.value==1 and byte.raw==b'G')


def _api():
    global _api_instance
    if _api_instance is None:_api_instance=WinAPI()
    return _api_instance


@contextmanager
def _lock(name, *, nonblocking=False):
    from platform_support import ensure_private_dir,open_nofollow,protect_private,flock,LOCK_EX,LOCK_NB,LOCK_UN
    ensure_private_dir(DIRECTORY)
    path=DIRECTORY/name
    fd=open_nofollow(path,os.O_CREAT|os.O_RDWR,0o600)
    acquired=False
    try:
        protect_private(path)
        flock(fd,LOCK_EX|(LOCK_NB if nonblocking else 0));acquired=True
        yield
    finally:
        if acquired:flock(fd,LOCK_UN)
        os.close(fd)


def _record_path():return DIRECTORY/'runtime.json'


def _write_record(record):
    from platform_support import open_nofollow,protect_private,reject_links
    path=_record_path();reject_links(path)
    temporary=DIRECTORY/('runtime-'+secrets.token_hex(8)+'.tmp')
    fd=open_nofollow(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        protect_private(temporary)
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            fd=None;json.dump(record,out,separators=(',',':'));out.flush();os.fsync(out.fileno())
        os.replace(temporary,path)
    finally:
        if fd is not None:os.close(fd)
        temporary.unlink(missing_ok=True)


def _read_record():
    from platform_support import private_file_ok,open_nofollow
    path=_record_path()
    if not path.exists():return None
    if not private_file_ok(path):raise ValueError('Runtime record privacy verification failed')
    fd=open_nofollow(path,os.O_RDONLY)
    with os.fdopen(fd,'rb') as source:raw=source.read(MAX_CONTROL+1)
    if len(raw)>MAX_CONTROL:raise ValueError('Invalid runtime record')
    record=json.loads(raw)
    keys={'v','install','identity','port','token'}
    if not isinstance(record,dict) or set(record)!=keys or record['v']!=1 or record['install']!=_install_id():
        raise ValueError('Runtime belongs to a different installation')
    identity=record['identity']
    if not isinstance(identity,dict) or set(identity)!={'pid','start','exe','sid','session'}:
        raise ValueError('Invalid process identity')
    if (type(identity['pid']) is not int or identity['pid']<=0 or
        not isinstance(identity['start'],str) or not identity['start'].isdigit() or
        not isinstance(identity['exe'],str) or not isinstance(identity['sid'],str) or
        type(identity['session']) is not int or identity['session']<=0 or
        type(record['port']) is not int or not 1<=record['port']<=65535 or
        not isinstance(record['token'],str) or len(record['token'])!=64):raise ValueError('Invalid runtime record')
    try:bytes.fromhex(record['token'])
    except ValueError:raise ValueError('Invalid runtime control identity') from None
    return record


def _same_process(record):
    current=_api().current();saved=record['identity']
    if saved['sid']!=current['sid'] or saved['session']!=current['session']:return False
    return _api().identity(saved['pid'])==saved


def _proof(token,data):return hmac.new(bytes.fromhex(token),data,hashlib.sha256).hexdigest()


def _rpc(record,operation):
    # Never sends the control secret itself, follows redirects, or uses proxies.
    if not _same_process(record):raise ValueError('Runtime process identity changed')
    nonce=secrets.token_hex(16)
    raw=json.dumps({'op':operation,'nonce':nonce},separators=(',',':')).encode()
    request=urllib.request.Request(f'http://127.0.0.1:{record["port"]}/control',data=raw,
        headers={'Content-Type':'application/json','X-Snowball-Proof':_proof(record['token'],raw)})
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,*args,**kwargs):return None
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=1) as response:
        payload=response.read(MAX_CONTROL+1)
        if response.status!=200 or len(payload)>MAX_CONTROL:raise ValueError('Invalid runtime response')
        expected=_proof(record['token'],nonce.encode()+b'\n'+payload)
        if not hmac.compare_digest(response.headers.get('X-Snowball-Proof',''),expected):raise ValueError('Runtime authentication failed')
    result=json.loads(payload)
    if not isinstance(result,dict) or result.get('install')!=_install_id():raise ValueError('Runtime response identity changed')
    return result


def status():
    """Sanitized snapshot. Unverified/unreachable must not be treated as stopped."""
    if not _windows():return {'supported':False,'running':False,'state':'stopped','children':{}}
    try:record=_read_record()
    except (OSError,ValueError):return {'supported':True,'running':False,'state':'unverified','children':{}}
    if record is None:return {'supported':True,'running':False,'state':'stopped','children':{}}
    try:
        if not _same_process(record):return {'supported':True,'running':False,'state':'unverified','children':{}}
        result=_rpc(record,'status')
        # Deep whitelist: no token or process command can leak even if a
        # future server extends its internal child diagnostic dictionaries.
        children={}
        reported=result.get('children',{})
        if not isinstance(reported,dict):raise ValueError('Invalid child diagnostics')
        for kind in ('hub','bridge'):
            child=reported.get(kind)
            if not isinstance(child,dict):continue
            state=child.get('state')
            if state not in ('running','backoff','waiting_hub','stopped'):state='unknown'
            pid=child.get('pid');restarts=child.get('restarts')
            children[kind]={'state':state,'pid':pid if type(pid) is int and pid>0 else None,
                'restarts':restarts if type(restarts) is int and restarts>=0 else 0}
            if kind=='hub':children[kind]['healthy']=child.get('healthy') is True
        return {'supported':True,'running':result.get('running') is True,
            'state':'running' if result.get('running') is True else 'stopped',
            'install':_install_id(),'children':children}
    except (OSError,ValueError):return {'supported':True,'running':False,'state':'unreachable','children':{}}


def _log_stream(name):
    from platform_support import open_nofollow,protect_private
    path=DIRECTORY/(name+'.log')
    fd=open_nofollow(path,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
    try:protect_private(path)
    except BaseException:os.close(fd);raise
    return os.fdopen(fd,'ab',buffering=0)


def start(*,executable=None,entry=None,timeout=12):
    _require_windows();prefix=launch_prefix(executable,entry);_api().current()
    with _lock('manager.lock'):
        existing=status()
        if existing.get('running'):return existing
        if existing['state']=='unreachable':raise RuntimeFailure('runtime_unreachable')
        # A stale/reused PID never authorizes termination. The lifetime lock is
        # the authority for replacing a stale record, not the recorded PID.
        try:
            with _lock('supervisor.lock',nonblocking=True):pass
        except BlockingIOError as error:raise RuntimeFailure('runtime_already_owned',cause=error) from None
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',4190))
            except OSError as error:raise RuntimeFailure('hub_port_unavailable',cause=error) from None
        with _log_stream('supervisor') as log:
            try:
                process=subprocess.Popen([*prefix,'run'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                    cwd=BASE,close_fds=True,creationflags=0x08000000|0x01000000|0x00000200)
            except OSError as error:
                # ERROR_ACCESS_DENIED alone is not proof of a Job restriction.
                # Do not retry without BREAKAWAY: that can die with the GUI.
                context={}
                if getattr(error,'winerror',None)==5:
                    try:context=_api().host_job_context()
                    except (OSError,RuntimeError):pass
                restricted=(context.get('in_job') is True and
                            context.get('breakaway_allowed') is False and
                            context.get('silent_breakaway') is False)
                raise RuntimeFailure('host_job_restricted' if restricted else 'supervisor_spawn_failed',
                                     cause=error,details=context) from None
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            exitcode=process.poll()
            if exitcode is not None:raise RuntimeFailure('supervisor_exited',details={'exitcode':exitcode})
            result=status()
            if result.get('running'):return result
            time.sleep(.1)
        # Only terminate the exact Popen handle we just created; never os.kill.
        process.terminate();process.wait(timeout=5)
        raise RuntimeFailure('supervisor_start_timeout')


def stop(*,timeout=12):
    _require_windows()
    with _lock('manager.lock'):
        record=_read_record()
        if record is None:return {'supported':True,'running':False,'state':'stopped','children':{}}
        if not _same_process(record):
            raise RuntimeFailure('runtime_identity_changed')
        _rpc(record,'stop')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if _api().identity(record['identity']['pid'])!=record['identity']:
                return {'supported':True,'running':False,'state':'stopped','children':{}}
            time.sleep(.1)
        raise RuntimeFailure('supervisor_stop_timeout')


class Supervisor:
    def __init__(self,prefix, *,clock=time.monotonic,popen=subprocess.Popen):
        self.prefix=prefix;self.clock=clock;self.popen=popen;self.stopping=threading.Event()
        self.job=_api().job();self.children={}
        for kind in ('hub','bridge'):
            self.children[kind]={'process':None,'state':'stopped','restarts':0,'failures':0,'next':0,'started':0}
        self.hub_ready=False

    def snapshot(self):
        return {'supported':True,'running':not self.stopping.is_set(),'state':'running',
            'install':_install_id(),'children':{k:{'state':v['state'],
            'pid':v['process'].pid if v['process'] is not None else None,'restarts':max(0,v['restarts']-1),
            **({'healthy':self.hub_ready} if k=='hub' else {})}
            for k,v in self.children.items()}}

    def _stop_child(self,child):
        p=child['process']
        if p is not None:
            if p.poll() is None:p.terminate()  # retained Windows HANDLE, not PID
            p.wait(timeout=5)
        child['process']=None;child['state']='stopped'

    def _spawn(self,kind,child):
        with _log_stream(kind) as log:
            p=self.popen([*self.prefix,'child',kind],stdin=subprocess.PIPE,stdout=log,stderr=log,
                cwd=BASE,close_fds=True,creationflags=0x08000000)
        try:
            _api().assign(self.job,p)
            p.stdin.write(b'G');p.stdin.flush();p.stdin.close()
        except BaseException:
            # The gate does not execute Hub/BLE if assignment or parent fails.
            if p.poll() is None:p.terminate()
            p.wait(timeout=5);raise
        child.update(process=p,state='running',started=self.clock(),restarts=child['restarts']+1)

    def _backoff(self,child):
        child['failures']+=1;child['state']='backoff'
        child['next']=self.clock()+min(60,2**min(child['failures']-1,6))

    def step(self):
        now=self.clock()
        for kind,child in self.children.items():
            p=child['process']
            if p is not None and p.poll() is not None:
                p.wait();child['process']=None;self._backoff(child)
            elif p is not None and now-child['started']>=60:child['failures']=0
            if kind=='bridge' and not self.hub_ready:
                self._stop_child(child);child['state']='waiting_hub';continue
            if child['process'] is None and now>=child['next']:
                try:self._spawn(kind,child)
                except (OSError,ValueError,RuntimeFailure) as error:
                    if sys.stderr is not None:
                        print(json.dumps(_error_record(error,'child_'+kind),ensure_ascii=False),file=sys.stderr)
                    self._backoff(child)
        hub=self.children['hub']
        self.hub_ready=bool(hub['process'] is not None and hub['process'].poll() is None and
                            now-hub['started']>=.25 and _hub_health())
        if not self.hub_ready and self.children['bridge']['process'] is not None:
            self._stop_child(self.children['bridge']);self.children['bridge']['state']='waiting_hub'

    def close(self):
        self.stopping.set()
        # Closing this private Job kills only its assigned children/descendants,
        # including on supervisor crash; unrelated existing Hub/Python survive.
        _api().close_job(self.job)
        for child in self.children.values():
            if child['process'] is not None:child['process'].wait(timeout=5)
            child['process']=None;child['state']='stopped'


def _hub_health():
    from platform_support import private_file_ok,open_nofollow
    path=Path.home()/'.config/xueqiu/hub-token'
    try:
        if not private_file_ok(path):return False
        fd=open_nofollow(path,os.O_RDONLY)
        with os.fdopen(fd,'r',encoding='utf-8') as source:token=source.read(130).strip()
        if not 32<=len(token)<=128:return False
        request=urllib.request.Request('http://127.0.0.1:4190/v1/health',headers={'Authorization':'Bearer '+token})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):return None
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(request,timeout=.3) as response:
            raw=response.read(1025)
            if response.status!=200 or len(raw)>1024:return False
        data=json.loads(raw);return data.get('ok') is True and data.get('hub_alive') is True
    except (OSError,ValueError):return False


def run(*,executable=None,entry=None):
    _require_windows();identity=_api().current();prefix=launch_prefix(executable,entry)
    with _lock('supervisor.lock',nonblocking=True):
        supervisor=Supervisor(prefix);token=secrets.token_hex(32)
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def setup(self):
                self.request.settimeout(.5)
                super().setup()
            def do_POST(self):
                if self.path!='/control':self.send_error(404);return
                try:
                    size=int(self.headers.get('Content-Length','0'))
                    if not 1<=size<=512 or self.headers.get('Transfer-Encoding'):raise ValueError()
                    raw=self.rfile.read(size)
                    if len(raw)!=size or not hmac.compare_digest(self.headers.get('X-Snowball-Proof',''),_proof(token,raw)):raise ValueError()
                    data=json.loads(raw)
                    if set(data)!={'op','nonce'} or data['op'] not in ('status','stop') or not isinstance(data['nonce'],str) or len(data['nonce'])!=32:raise ValueError()
                    result=supervisor.snapshot();body=json.dumps(result,separators=(',',':')).encode()
                    self.send_response(200);self.send_header('Content-Length',str(len(body)))
                    self.send_header('X-Snowball-Proof',_proof(token,data['nonce'].encode()+b'\n'+body));self.end_headers();self.wfile.write(body)
                    if data['op']=='stop':supervisor.stopping.set()
                except (OSError,ValueError,TypeError):self.send_error(403)
        server=None;record=None
        try:
            server=HTTPServer(('127.0.0.1',0),Handler);server.timeout=.2
            record={'v':1,'install':_install_id(),'identity':identity,'port':server.server_port,'token':token}
            _write_record(record)
            while not supervisor.stopping.is_set():
                supervisor.step();server.handle_request()
        finally:
            if server is not None:server.server_close()
            supervisor.close()
            try:
                if record is not None and _read_record()==record:_record_path().unlink()
            except (OSError,ValueError):pass
    return 0


def _task_name(sid):return 'SnowballCompanion-'+hashlib.sha256(sid.encode()).hexdigest()[:16]


def _task_marker(sid):return 'Snowball managed runtime v1:'+_install_id()+':'+sid


def _task_xml(prefix,sid):
    ET.register_namespace('',TASK_NS)
    def element(parent,name,text=None):
        node=ET.SubElement(parent,'{'+TASK_NS+'}'+name)
        if text is not None:node.text=text
        return node
    task=ET.Element('{'+TASK_NS+'}Task',version='1.2')
    registration=element(task,'RegistrationInfo');element(registration,'Description',_task_marker(sid))
    triggers=element(task,'Triggers');trigger=element(triggers,'LogonTrigger');element(trigger,'Enabled','true');element(trigger,'UserId',sid)
    principals=element(task,'Principals');principal=element(principals,'Principal');principal.set('id','User')
    element(principal,'UserId',sid);element(principal,'LogonType','InteractiveToken');element(principal,'RunLevel','LeastPrivilege')
    settings=element(task,'Settings')
    for name,value in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),('AllowStartOnDemand','true'),('StartWhenAvailable','true'),('ExecutionTimeLimit','PT0S'),('Enabled','true')]:element(settings,name,value)
    restart=element(settings,'RestartOnFailure');element(restart,'Interval','PT1M');element(restart,'Count','3')
    actions=element(task,'Actions');actions.set('Context','User');action=element(actions,'Exec')
    element(action,'Command',prefix[0]);element(action,'Arguments',subprocess.list2cmdline([*prefix[1:],'run']))
    element(action,'WorkingDirectory',str(BASE))
    return ET.tostring(task,encoding='unicode',xml_declaration=False)


def _task_user_matches(value,sid):
    if value==sid:return True
    # A different literal SID must never be reinterpreted as an account name.
    if not isinstance(value,str) or not value or value.upper().startswith('S-'):return False
    try:return _api().account_sid(value)==sid
    except (OSError,ValueError,RuntimeError):return False


def _task_ownership_checks(xml,prefix,sid):
    """Safe boolean diagnostics; paths, account names and XML are never returned."""
    try:
        root=ET.fromstring(xml);ns={'t':TASK_NS}
        principals=root.findall('t:Principals/t:Principal',ns);actions=root.findall('t:Actions/*',ns)
        triggers=root.findall('t:Triggers/*',ns)
        levels=root.findall('t:Principals/t:Principal/t:RunLevel',ns)
        text=lambda path:root.findtext(path,namespaces=ns)
        # The Scheduler's documented default is LUA and its XML schema makes
        # RunLevel optional. Only an absent element gets that default; empty,
        # unknown, duplicate or explicit HighestAvailable remains rejected.
        least_privilege=not levels or (len(levels)==1 and levels[0].text=='LeastPrivilege')
        return dict(zip(TASK_CHECKS,(
            len(principals)==1,len(actions)==1,len(triggers)==1,
            text('t:RegistrationInfo/t:Description')==_task_marker(sid),
            _task_user_matches(text('t:Principals/t:Principal/t:UserId'),sid),
            text('t:Principals/t:Principal/t:LogonType')=='InteractiveToken',
            least_privilege,
            _task_user_matches(text('t:Triggers/t:LogonTrigger/t:UserId'),sid),
            os.path.normcase(text('t:Actions/t:Exec/t:Command') or '')==os.path.normcase(prefix[0]),
            text('t:Actions/t:Exec/t:Arguments')==subprocess.list2cmdline([*prefix[1:],'run']),
            text('t:Actions/t:Exec/t:WorkingDirectory')==str(BASE))))
    except ET.ParseError:return dict.fromkeys(TASK_CHECKS,False)


def _owned_task(xml,prefix,sid):
    return all(_task_ownership_checks(xml,prefix,sid).values())


def _decode_task_output(raw,encoding):
    # schtasks may emit a BOM or the local console code page while its XML
    # declaration says UTF-16. Decode transport bytes before parsing Unicode.
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):text=raw.decode('utf-16')
    # BOM-less UTF-16 may have CR/LF/space before the opening XML token.
    elif raw.startswith((b'<\x00',b'\r\x00',b'\n\x00',b' \x00',b'\t\x00')):text=raw.decode('utf-16-le')
    elif raw.startswith((b'\x00<',b'\x00\r',b'\x00\n',b'\x00 ',b'\x00\t')):text=raw.decode('utf-16-be')
    else:
        try:text=raw.decode('utf-8-sig')
        except UnicodeDecodeError:text=raw.decode(encoding)
    # schtasks can prefix a transport newline before the XML declaration.
    # Do not normalize any account, path or argument text inside the document.
    return text.lstrip('\ufeff \t\r\n')


def _schtasks(args):
    result=subprocess.run([_api().task_tool(),*args],capture_output=True,timeout=20,
                          creationflags=0x08000000)
    # Never expose stderr, which may contain paths/account data. Only XML is
    # consumed. Decode failures are unavailable, never an ownership match.
    result.stdout=_decode_task_output(result.stdout,_api().task_encoding())
    result.stderr=''
    return result


TASK_QUERY_SCRIPT = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    $service = New-Object -ComObject 'Schedule.Service'
    $service.Connect()
    $task = $service.GetFolder('\').GetTask($env:SNOWBALL_TASK_NAME)
    $result = @{v=1;ok=$true;xml=[string]$task.Xml}
    [Console]::Out.Write(($result | ConvertTo-Json -Compress -Depth 3))
    exit 0
} catch {
    $exception = $_.Exception
    while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
    $code = ([int64]$exception.HResult) -band 4294967295L
    [Console]::Out.Write((@{v=1;ok=$false;hresult=$code} | ConvertTo-Json -Compress))
    exit 1
}
"""


def _query_task(name):
    """Read IRegisteredTask.Xml as Unicode, not schtasks' lossy ANSI export.

    No account, path or XML is interpolated into PowerShell. The fixed task name
    is passed through the environment. COM HRESULTs preserve not-found versus
    access-denied/service errors; raw PowerShell stderr is never returned.
    """
    suffix=name.removeprefix('SnowballCompanion-') if isinstance(name,str) else ''
    if (not isinstance(name,str) or name!='SnowballCompanion-'+suffix or len(suffix)!=16 or
        any(c not in '0123456789abcdef' for c in suffix)):
        raise ValueError('Invalid owned task name')
    powershell=Path(_api().task_tool()).parent/'WindowsPowerShell'/'v1.0'/'powershell.exe'
    encoded=base64.b64encode(TASK_QUERY_SCRIPT.encode('utf-16-le')).decode('ascii')
    try:
        native=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-EncodedCommand',encoded],
            env={**os.environ,'SNOWBALL_TASK_NAME':name},capture_output=True,timeout=20,creationflags=0x08000000)
    except (OSError,subprocess.SubprocessError) as error:
        raise RuntimeFailure('task_query_failed',cause=error) from None
    # E_FAIL on invalid transport; never infer absence from a decoder failure.
    result=subprocess.CompletedProcess([],0x80004005,stdout='',stderr='')
    try:
        if len(native.stdout)>262144:return result
        payload=json.loads(native.stdout.decode('utf-8-sig'))
        if not isinstance(payload,dict) or type(payload.get('v')) is not int or payload['v']!=1:return result
        if (native.returncode==0 and payload.get('ok') is True and
            set(payload)=={'v','ok','xml'} and isinstance(payload['xml'],str)):
            result.returncode=0;result.stdout=payload['xml']
        elif (native.returncode!=0 and payload.get('ok') is False and
              set(payload)=={'v','ok','hresult'} and type(payload['hresult']) is int and
              0<payload['hresult']<=0xffffffff):
            result.returncode=payload['hresult']
    except (UnicodeError,ValueError):pass
    return result


def _task_absent(result):
    # Official /HRESULT output distinguishes not-found from access denied,
    # unavailable Scheduler, or localized error text without parsing messages.
    return result.returncode & 0xffffffff in (0x80070002,0x80070003)


def autostart_status(*,executable=None,entry=None):
    """Read-only ownership report. Query errors are not proof of absence."""
    if not _windows():return {'supported':False,'installed':None,'owned':False,'state':'unsupported'}
    sid=_api().current()['sid'];prefix=launch_prefix(executable,entry);name=_task_name(sid)
    query=_query_task(name)
    if query.returncode:return {'supported':True,'installed':False if _task_absent(query) else None,'owned':False,'state':'absent' if _task_absent(query) else 'unavailable','task':name}
    owned=_owned_task(query.stdout,prefix,sid)
    return {'supported':True,'installed':True,'owned':owned,'state':'owned' if owned else 'other','task':name}


def install_autostart(*,executable=None,entry=None,user_sid=None):
    _require_windows();sid=_api().current()['sid'];prefix=launch_prefix(executable,entry)
    if user_sid is not None and user_sid!=sid:raise ValueError('Cross-user task registration is refused')
    with _lock('manager.lock'):
        name=_task_name(sid);query=_query_task(name)
        replace=query.returncode==0
        if query.returncode and not _task_absent(query):raise RuntimeFailure('task_query_failed',details={'exitcode':query.returncode})
        if replace and not _owned_task(query.stdout,prefix,sid):raise ValueError('Existing task is not owned by this user installation')
        path=DIRECTORY/('autostart-'+secrets.token_hex(8)+'.xml')
        from platform_support import open_nofollow,protect_private
        fd=open_nofollow(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            protect_private(path)
            with os.fdopen(fd,'w',encoding='utf-16') as out:fd=None;out.write(_task_xml(prefix,sid))
            result=_schtasks(['/Create','/TN',name,'/XML',str(path)]+(['/F'] if replace else []))
            if result.returncode:raise RuntimeFailure('task_create_failed',details={'exitcode':result.returncode})
            verify=_query_task(name)
            checks=_task_ownership_checks(verify.stdout,prefix,sid)
            if verify.returncode or not all(checks.values()):
                raise RuntimeFailure('task_create_unverified',details={'exitcode':verify.returncode,'task_checks':checks})
        finally:
            if fd is not None:os.close(fd)
            path.unlink(missing_ok=True)
    return {'installed':True,'task':name,'interactive':True,'elevated':False}


def remove_autostart(*,executable=None,entry=None,user_sid=None):
    _require_windows();sid=_api().current()['sid'];prefix=launch_prefix(executable,entry)
    if user_sid is not None and user_sid!=sid:raise ValueError('Cross-user task removal is refused')
    with _lock('manager.lock'):
        name=_task_name(sid);query=_query_task(name)
        if _task_absent(query):return {'removed':False,'state':'absent'}
        if query.returncode:raise RuntimeFailure('task_query_failed',details={'exitcode':query.returncode})
        if not _owned_task(query.stdout,prefix,sid):raise ValueError('Existing task is not owned by this user installation')
        deleted=_schtasks(['/Delete','/TN',name,'/F'])
        if deleted.returncode:raise RuntimeFailure('task_delete_failed',details={'exitcode':deleted.returncode})
        verified=_query_task(name)
        if not _task_absent(verified):raise RuntimeFailure('task_delete_unverified',details={'exitcode':verified.returncode})
    return {'removed':True,'running_unchanged':True}


def child(kind):
    _require_windows()
    if kind not in ('hub','bridge') or not _api().gate():return 2
    module='status_hub' if kind=='hub' else 'bridge'
    sys.argv=[module]+([] if kind=='hub' else ['watch','--source',str(STATE/'hub/snapshot.json')])
    importlib.import_module(module).main()
    return 0


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['run','start','stop','status','install-autostart','remove-autostart','autostart-status','child'])
    parser.add_argument('kind',nargs='?',choices=['hub','bridge'])
    args=parser.parse_args(argv)
    try:
        if args.command=='run':return run()
        if args.command=='child':return child(args.kind)
        if args.kind:parser.error('kind is only used by child')
        result=globals()[args.command.replace('-','_')]();print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('state') not in ('unverified','unreachable','unavailable') else 2
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as error:
        # Do not echo process arguments, credentials, raw OS/task output.
        if sys.stderr is not None:
            print(json.dumps(_error_record(error,args.command),ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
