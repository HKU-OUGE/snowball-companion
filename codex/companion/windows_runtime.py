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


def _windows():
    return sys.platform == 'win32'


def _require_windows():
    if not _windows():
        raise RuntimeError('Windows interactive runtime is only available on Windows')


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

    def task_tool(self):
        path=self.c.create_unicode_buffer(32768)
        count=self.k.GetSystemDirectoryW(path,len(path))
        if not 0<count<len(path):raise OSError('Cannot locate system task tool')
        return str(Path(path.value)/'schtasks.exe')

    def task_encoding(self):
        return 'cp'+str(self.k.GetConsoleOutputCP() or self.k.GetOEMCP())

    def _sid(self, process):
        c,w=self.c,self.w;token=w.HANDLE()
        if not self.a.OpenProcessToken(process,8,c.byref(token)):raise OSError('Cannot inspect process owner')
        try:
            size=w.DWORD();self.a.GetTokenInformation(token,1,None,0,c.byref(size))
            buf=c.create_string_buffer(size.value)
            if not self.a.GetTokenInformation(token,1,buf,size,c.byref(size)):raise OSError('Cannot inspect process token')
            pointer=c.cast(buf,c.POINTER(c.c_void_p))[0]; text=w.LPWSTR()
            if not self.a.ConvertSidToStringSidW(pointer,c.byref(text)):raise OSError('Cannot inspect process SID')
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
        if not identity or identity['session']==0:raise RuntimeError('Use the signed-in interactive Windows session, not Session 0')
        return identity

    def job(self):
        c,w=self.c,self.w
        class Basic(c.Structure):
            _fields_=[('process_time',c.c_int64),('job_time',c.c_int64),('flags',w.DWORD),
                      ('min_ws',c.c_size_t),('max_ws',c.c_size_t),('active',w.DWORD),
                      ('affinity',c.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class IO(c.Structure):_fields_=[('values',c.c_uint64*6)]
        class Extended(c.Structure):
            _fields_=[('basic',Basic),('io',IO),('process_mem',c.c_size_t),('job_mem',c.c_size_t),
                      ('peak_process',c.c_size_t),('peak_job',c.c_size_t)]
        handle=self.k.CreateJobObjectW(None,None)
        if not handle:raise OSError('Cannot create owned child job')
        limits=Extended();limits.basic.flags=0x2000  # KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(handle,9,c.byref(limits),c.sizeof(limits)):
            self.k.CloseHandle(handle);raise OSError('Cannot set owned job lifetime')
        return handle

    def assign(self,job,child):
        if not self.k.AssignProcessToJobObject(job,int(child._handle)):
            raise OSError('Cannot contain the owned child process')

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
        if existing['state']=='unreachable':raise RuntimeError('Owned runtime is not responding; inspect its private log')
        # A stale/reused PID never authorizes termination. The lifetime lock is
        # the authority for replacing a stale record, not the recorded PID.
        try:
            with _lock('supervisor.lock',nonblocking=True):pass
        except BlockingIOError:raise RuntimeError('Another supervisor owns this user runtime') from None
        with socket.socket() as probe:
            try:probe.bind(('127.0.0.1',4190))
            except OSError:raise RuntimeError('Hub port is occupied; existing process was not stopped') from None
        with _log_stream('supervisor') as log:
            process=subprocess.Popen([*prefix,'run'],stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                cwd=BASE,close_fds=True,creationflags=0x08000000|0x01000000|0x00000200)
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if process.poll() is not None:raise RuntimeError('Supervisor startup failed; inspect its private log')
            result=status()
            if result.get('running'):return result
            time.sleep(.1)
        # Only terminate the exact Popen handle we just created; never os.kill.
        process.terminate();process.wait(timeout=5)
        raise RuntimeError('Supervisor startup timed out; its own process was stopped')


def stop(*,timeout=12):
    _require_windows()
    with _lock('manager.lock'):
        record=_read_record()
        if record is None:return {'supported':True,'running':False,'state':'stopped','children':{}}
        if not _same_process(record):
            raise RuntimeError('Process identity is stale or belongs to another session; nothing was stopped')
        _rpc(record,'stop')
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if _api().identity(record['identity']['pid'])!=record['identity']:
                return {'supported':True,'running':False,'state':'stopped','children':{}}
            time.sleep(.1)
        raise RuntimeError('Graceful supervisor shutdown was not confirmed; no other process was signaled')


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
                except (OSError,ValueError):self._backoff(child)
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


def _owned_task(xml,prefix,sid):
    try:
        root=ET.fromstring(xml);ns={'t':TASK_NS}
        principals=root.findall('t:Principals/t:Principal',ns);actions=root.findall('t:Actions/*',ns)
        triggers=root.findall('t:Triggers/*',ns)
        text=lambda path:root.findtext(path,namespaces=ns)
        return (len(principals)==len(actions)==len(triggers)==1 and
            text('t:RegistrationInfo/t:Description')==_task_marker(sid) and
            text('t:Principals/t:Principal/t:UserId')==sid and
            text('t:Principals/t:Principal/t:LogonType')=='InteractiveToken' and
            text('t:Principals/t:Principal/t:RunLevel')=='LeastPrivilege' and
            text('t:Triggers/t:LogonTrigger/t:UserId')==sid and
            os.path.normcase(text('t:Actions/t:Exec/t:Command') or '')==os.path.normcase(prefix[0]) and
            text('t:Actions/t:Exec/t:Arguments')==subprocess.list2cmdline([*prefix[1:],'run']) and
            text('t:Actions/t:Exec/t:WorkingDirectory')==str(BASE))
    except ET.ParseError:return False


def _decode_task_output(raw,encoding):
    # schtasks may emit a BOM or the local console code page while its XML
    # declaration says UTF-16. Decode transport bytes before parsing Unicode.
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):return raw.decode('utf-16')
    if raw.startswith(b'<\x00'):return raw.decode('utf-16-le')
    try:return raw.decode('utf-8-sig')
    except UnicodeDecodeError:return raw.decode(encoding)


def _schtasks(args):
    result=subprocess.run([_api().task_tool(),*args],capture_output=True,timeout=20,
                          creationflags=0x08000000)
    # Never expose stderr, which may contain paths/account data. Only XML is
    # consumed. Decode failures are unavailable, never an ownership match.
    result.stdout=_decode_task_output(result.stdout,_api().task_encoding())
    result.stderr=''
    return result


def _query_task(name):return _schtasks(['/Query','/TN',name,'/XML','/HRESULT'])


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
        if query.returncode and not _task_absent(query):raise RuntimeError('Task Scheduler ownership query unavailable')
        if replace and not _owned_task(query.stdout,prefix,sid):raise ValueError('Existing task is not owned by this user installation')
        path=DIRECTORY/('autostart-'+secrets.token_hex(8)+'.xml')
        from platform_support import open_nofollow,protect_private
        fd=open_nofollow(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        try:
            protect_private(path)
            with os.fdopen(fd,'w',encoding='utf-16') as out:fd=None;out.write(_task_xml(prefix,sid))
            result=_schtasks(['/Create','/TN',name,'/XML',str(path)]+(['/F'] if replace else []))
            if result.returncode:raise RuntimeError('Interactive task registration failed; no password or elevated service was used')
            verify=_query_task(name)
            if verify.returncode or not _owned_task(verify.stdout,prefix,sid):raise RuntimeError('Task registration could not be verified')
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
        if query.returncode:raise RuntimeError('Task Scheduler ownership query unavailable')
        if not _owned_task(query.stdout,prefix,sid):raise ValueError('Existing task is not owned by this user installation')
        if _schtasks(['/Delete','/TN',name,'/F']).returncode:raise RuntimeError('Owned task removal failed')
        if not _task_absent(_query_task(name)):raise RuntimeError('Owned task removal was not confirmed')
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
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):
        # Do not echo process arguments, credentials, raw OS/task output.
        print(json.dumps({'ok':False,'error':'Windows runtime operation failed; inspect private runtime logs'}),file=sys.stderr)
        return 2


if __name__=='__main__':raise SystemExit(main())
