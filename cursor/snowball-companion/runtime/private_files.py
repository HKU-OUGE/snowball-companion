"""Private local files: POSIX modes or verified Windows user/SYSTEM DACLs.

Reject symlinks and Windows reparse points instead of silently following them.
Never relax the final firmware hash/signature checks performed by callers.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import tempfile
import time


def checked_path(path):
    path = Path(path).absolute()
    for item in [*reversed(path.parents), path]:
        try: info = item.lstat()
        except FileNotFoundError: continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError('Refusing linked or reparse-point path')
    return path


def windows_private(path):
    """Set and read back a protected DACL with exactly user and SYSTEM access."""
    import ctypes as c
    from ctypes import wintypes as w
    import _winapi
    k = c.WinDLL('kernel32', use_last_error=True)
    a = c.WinDLL('advapi32', use_last_error=True)
    a.OpenProcessToken.argtypes = [w.HANDLE,w.DWORD,c.POINTER(w.HANDLE)]
    a.GetTokenInformation.argtypes = [w.HANDLE,c.c_int,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
    a.ConvertSidToStringSidW.argtypes = [w.LPVOID,c.POINTER(w.LPWSTR)]
    a.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR,w.DWORD,c.POINTER(w.LPVOID),c.POINTER(w.DWORD)]
    a.SetKernelObjectSecurity.argtypes = [w.HANDLE,w.DWORD,w.LPVOID]
    a.GetKernelObjectSecurity.argtypes = [w.HANDLE,w.DWORD,w.LPVOID,w.DWORD,c.POINTER(w.DWORD)]
    a.GetSecurityDescriptorDacl.argtypes = [w.LPVOID,c.POINTER(w.BOOL),c.POINTER(w.LPVOID),c.POINTER(w.BOOL)]
    a.GetSecurityDescriptorControl.argtypes = [w.LPVOID,c.POINTER(w.WORD),c.POINTER(w.DWORD)]
    a.GetAclInformation.argtypes = [w.LPVOID,w.LPVOID,w.DWORD,c.c_int]
    a.GetAce.argtypes = [w.LPVOID,w.DWORD,c.POINTER(w.LPVOID)]
    k.LocalFree.argtypes = [w.LPVOID]
    k.GetFileInformationByHandleEx.argtypes = [w.HANDLE,c.c_int,w.LPVOID,w.DWORD]
    def require(ok):
        if not ok: raise c.WinError(c.get_last_error())
    def sid_string(sid):
        text = w.LPWSTR()
        require(a.ConvertSidToStringSidW(sid,c.byref(text)))
        try: return text.value
        finally: k.LocalFree(c.cast(text,w.LPVOID))
    token = w.HANDLE()
    require(a.OpenProcessToken(_winapi.GetCurrentProcess(),8,c.byref(token)))
    try:
        size = w.DWORD()
        a.GetTokenInformation(token,1,None,0,c.byref(size))
        data = c.create_string_buffer(size.value)
        require(a.GetTokenInformation(token,1,data,size,c.byref(size)))
        sid = sid_string(c.cast(data,c.POINTER(w.LPVOID))[0])
    finally: _winapi.CloseHandle(token.value)
    path = checked_path(path)
    directory = path.is_dir()
    flags = 'OICI' if directory else ''
    sddl = f'D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;{sid})'
    descriptor = w.LPVOID()
    require(a.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl,1,c.byref(descriptor),None))
    handle = None
    try:
        handle = _winapi.CreateFile(str(path),0x00060080,3,0,3,0x02200000,0)
        attributes = (w.DWORD*2)()
        require(k.GetFileInformationByHandleEx(handle,9,attributes,c.sizeof(attributes)))
        if attributes[0] & 0x400: raise OSError('Refusing reparse-point security target')
        def verify():
            size = w.DWORD()
            a.GetKernelObjectSecurity(handle,4,None,0,c.byref(size))
            actual = c.create_string_buffer(size.value)
            require(a.GetKernelObjectSecurity(handle,4,actual,size,c.byref(size)))
            control, revision = w.WORD(), w.DWORD()
            require(a.GetSecurityDescriptorControl(actual,c.byref(control),c.byref(revision)))
            present, defaulted, acl = w.BOOL(), w.BOOL(), w.LPVOID()
            require(a.GetSecurityDescriptorDacl(actual,c.byref(present),c.byref(acl),c.byref(defaulted)))
            class AclSize(c.Structure):
                _fields_=[('count',w.DWORD),('used',w.DWORD),('free',w.DWORD)]
            size_info = AclSize()
            if not present or not acl: raise OSError('Private DACL missing')
            require(a.GetAclInformation(acl,c.byref(size_info),c.sizeof(size_info),2))
            if size_info.count != 2 or not control.value & 0x1000:
                raise OSError('Private DACL not protected')
            found = set()
            for index in range(2):
                ace = w.LPVOID()
                require(a.GetAce(acl,index,c.byref(ace)))
                header = c.string_at(ace,8)
                mask = int.from_bytes(header[4:8],'little')
                if header[0] != 0 or header[1] != (3 if directory else 0) or mask != 0x1f01ff:
                    raise OSError('Unexpected private DACL entry')
                found.add(sid_string(ace.value+8))
            if found != {sid,'S-1-5-18'}: raise OSError('Unexpected private DACL owner')
        try:
            verify()
        except OSError:
            require(a.SetKernelObjectSecurity(handle,0x80000004,descriptor))
            verify()
    finally:
        if handle is not None: _winapi.CloseHandle(handle)
        k.LocalFree(descriptor)


def private_dir(path):
    path = checked_path(path)
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    checked_path(path)
    if os.name == 'nt': windows_private(path)
    else: path.chmod(0o700)
    return path


def private_file(path):
    path = checked_path(path)
    if os.name == 'nt': windows_private(path)
    else: path.chmod(0o600)


@contextmanager
def safe_reader(path):
    if os.name == 'nt':
        import _winapi
        import ctypes as c
        from ctypes import wintypes as w
        import msvcrt
        # READ|DELETE sharing keeps an open snapshot valid across atomic replace.
        # Retry transient replacement gaps and sharing failures, with a hard bound;
        # reparse-point checks still run on every attempt and on the final handle.
        deadline = time.monotonic() + .25
        while True:
            try:
                path = checked_path(path)
                handle = _winapi.CreateFile(str(path),0x80000000,5,0,3,0x00200000,0)
                break
            except OSError as error:
                if getattr(error,'winerror',None) not in (2,5,32,33) or time.monotonic()>=deadline:raise
                time.sleep(.005)
        try:
            k = c.WinDLL('kernel32',use_last_error=True)
            k.GetFileInformationByHandleEx.argtypes = [w.HANDLE,c.c_int,w.LPVOID,w.DWORD]
            info = (w.DWORD*2)()
            if not k.GetFileInformationByHandleEx(handle,9,info,c.sizeof(info)):
                raise c.WinError(c.get_last_error())
            if info[0] & (0x400|0x10): raise OSError('Refusing reparse point or directory')
            fd = msvcrt.open_osfhandle(handle,os.O_RDONLY|os.O_BINARY)
            handle = None
        finally:
            if handle is not None: _winapi.CloseHandle(handle)
    else:
        path = checked_path(path)
        fd = os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd,'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode): raise OSError('Not a regular file')
        yield stream


def read_text(path):
    """UTF-8 snapshot read using the platform's atomic-replacement sharing rules."""
    with safe_reader(path) as stream:
        return stream.read().decode('utf-8')


def private_write(path, content):
    path = checked_path(path)
    private_dir(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.private-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        private_file(temporary)
        checked_path(path)
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
