"""Small OS boundary shared verbatim by the standalone Snowball entry points.

Windows secrets use an explicit protected DACL and current-user owner check.
Unsupported ACE forms fail closed; these helpers never treat chmod as a Windows
privacy guarantee.
"""
import errno
import os
from pathlib import Path
import shlex
import stat
import subprocess

LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8
_WINDOWS = os.name == 'nt'


def command_line(argv):
    """Encode argv for CreateProcess on Windows, POSIX shell otherwise.

    Windows output is NOT a PowerShell or cmd.exe script. Prefer passing the
    original argv list to subprocess whenever the caller controls execution.
    """
    return subprocess.list2cmdline(list(argv)) if _WINDOWS else shlex.join(argv)


def reject_links(path):
    """Reject final symlinks and all Windows ancestor reparse points."""
    path = Path(os.path.abspath(path))
    candidates = (path, *path.parents) if _WINDOWS else (path,)
    for item in candidates:
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise OSError(errno.ELOOP, 'Link/reparse path refused', str(item))


def private_file_ok(path):
    """Fail closed for missing, linked, nonregular or nonprivate token files."""
    try:
        path = Path(os.path.abspath(path))
        reject_links(path)
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return False
        if _WINDOWS:
            return _windows_private(path)
        return info.st_uid == os.getuid() and not (info.st_mode & 0o077)
    except (OSError, ValueError):
        return False


def protect_private(path):
    """Set owner-only Unix modes or a protected current-user+SYSTEM DACL.

    Accepts regular files and directories; rejects links/reparse paths. Raises
    OSError on failure. Existing Windows objects must belong to this user.
    """
    path = Path(path)
    reject_links(path)
    info = path.stat()
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise OSError(errno.EINVAL, 'Expected a file or directory', str(path))
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise OSError(errno.EPERM, 'Hard-linked private file refused', str(path))
    if not _WINDOWS:
        if info.st_uid != os.getuid():
            raise PermissionError(errno.EPERM, 'Private object must belong to current user')
        os.chmod(path, 0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
        return
    sid = _current_sid()
    owner, _, descriptor = _security_info(path)
    try:
        if _sid_text(owner) != sid:
            raise PermissionError(errno.EPERM, 'Private object must belong to current user')
    finally:
        _kernel.LocalFree(descriptor)
    inheritance = 'OICI' if stat.S_ISDIR(info.st_mode) else ''
    sddl = f'D:P(A;{inheritance};FA;;;{sid})(A;{inheritance};FA;;;SY)'
    descriptor = ctypes.c_void_p()
    if not _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not _advapi.SetFileSecurityW(str(path.absolute()), 0x80000004, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        _kernel.LocalFree(descriptor)


def ensure_private_dir(path):
    path = Path(path)
    reject_links(path)
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    protect_private(path)
    return path


if _WINDOWS:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    class _Overlapped(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    class _FileInfo(ctypes.Structure):
        _fields_ = [('attributes', wintypes.DWORD), ('created', wintypes.FILETIME),
                    ('accessed', wintypes.FILETIME), ('written', wintypes.FILETIME),
                    ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                    ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                    ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]

    _kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    _kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.LockFileEx.restype = wintypes.BOOL
    _kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                    wintypes.DWORD, ctypes.POINTER(_Overlapped)]
    _kernel.UnlockFileEx.restype = wintypes.BOOL
    _kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_FileInfo)]
    _kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype = wintypes.BOOL

    _kernel.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel.LocalFree.restype = ctypes.c_void_p
    _kernel.GetCurrentProcess.restype = wintypes.HANDLE
    _advapi = ctypes.WinDLL('advapi32', use_last_error=True)
    _advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
    _advapi.OpenProcessToken.restype = wintypes.BOOL
    _advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _advapi.GetTokenInformation.restype = wintypes.BOOL
    _advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    _advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    _advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    _advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _advapi.GetAce.restype = wintypes.BOOL
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [wintypes.LPCWSTR,
        wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    _advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    _advapi.SetFileSecurityW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
    _advapi.SetFileSecurityW.restype = wintypes.BOOL

    class _Acl(ctypes.Structure):
        _fields_ = [('revision', ctypes.c_ubyte), ('reserved', ctypes.c_ubyte),
                    ('size', wintypes.WORD), ('count', wintypes.WORD), ('reserved2', wintypes.WORD)]

    def _sid_text(pointer):
        text = ctypes.c_void_p()
        if not _advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.wstring_at(text)
        finally:
            _kernel.LocalFree(text)

    def _current_sid():
        token = wintypes.HANDLE()
        if not _advapi.OpenProcessToken(_kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            size = wintypes.DWORD()
            _advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            data = ctypes.create_string_buffer(size.value)
            if not _advapi.GetTokenInformation(token, 1, data, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            return _sid_text(ctypes.c_void_p.from_buffer(data).value)
        finally:
            _kernel.CloseHandle(token)

    def _security_info(path):
        owner, dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        error = _advapi.GetNamedSecurityInfoW(str(Path(path).absolute()), 1, 5,
            ctypes.byref(owner), None, ctypes.byref(dacl), None, ctypes.byref(descriptor))
        if error:
            raise ctypes.WinError(error)
        return owner, dacl, descriptor

    def _windows_private(path):
        owner, dacl, descriptor = _security_info(path)
        try:
            sid = _current_sid()
            if _sid_text(owner) != sid or not dacl.value:
                return False
            acl = _Acl.from_address(dacl.value)
            allowed = {sid, 'S-1-5-18', 'S-1-5-32-544'}
            for index in range(acl.count):
                ace = ctypes.c_void_p()
                if not _advapi.GetAce(dacl, index, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                kind = ctypes.c_ubyte.from_address(ace.value).value
                if kind == 1:  # ACCESS_DENIED_ACE cannot grant access.
                    continue
                if kind != 0:  # Object/callback ACE semantics are not silently accepted.
                    return False
                if _sid_text(ace.value + 8) not in allowed:
                    return False
            return True
        finally:
            _kernel.LocalFree(descriptor)


def flock(file_or_fd, flags):
    """Exclusive advisory lock; Windows uses LockFileEx, never writes padding.

    Lock owners must keep their descriptor open until unlock/close. On Windows
    the byte-range lock is mandatory; use separate lock files for shared data.
    """
    fd = file_or_fd if isinstance(file_or_fd, int) else file_or_fd.fileno()
    if flags not in (LOCK_EX, LOCK_EX | LOCK_NB, LOCK_UN):
        raise ValueError('Only exclusive, nonblocking exclusive, and unlock are supported')
    if not _WINDOWS:
        import fcntl
        return fcntl.flock(fd, flags)
    handle = msvcrt.get_osfhandle(fd)
    overlap = _Overlapped()
    if flags == LOCK_UN:
        ok = _kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
    else:
        ok = _kernel.LockFileEx(handle, 2 | (1 if flags & LOCK_NB else 0),
                                0, 1, 0, ctypes.byref(overlap))
    if not ok:
        error = ctypes.get_last_error()
        if error == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EAGAIN, 'File lock is held')
        raise ctypes.WinError(error)


def open_nofollow(path, flags, mode=0o600):
    """Open a regular file without following the final link/reparse point.

    Windows ancestor reparse points are rejected as well. Existing parent
    directories must be trusted: pathname ancestor checks are not a sandbox
    against a concurrent malicious directory rename.
    """
    if not _WINDOWS:
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    reject_links(path)
    access_mode = flags & (os.O_WRONLY | os.O_RDWR)
    access = 0x80000000 if access_mode == os.O_RDONLY else (
        0x40000000 if access_mode == os.O_WRONLY else 0xC0000000)
    creation = (1 if flags & os.O_EXCL else 4) if flags & os.O_CREAT else 3
    handle = _kernel.CreateFileW(str(Path(path).absolute()), access, 7, None,
                                 creation, 0x00200000, None)  # OPEN_REPARSE_POINT
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _FileInfo()
        if not _kernel.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if info.attributes & (0x400 | 0x10):
            raise OSError(errno.ELOOP, 'Reparse/directory handle refused', str(path))
        fd = msvcrt.open_osfhandle(handle, access_mode | os.O_BINARY | (flags & os.O_APPEND))
        handle = None  # fd owns the handle now
        try:
            if flags & os.O_TRUNC:
                os.ftruncate(fd, 0)  # Only after the handle has been checked.
            return fd
        except BaseException:
            os.close(fd)
            raise
    finally:
        if handle is not None:
            _kernel.CloseHandle(handle)
