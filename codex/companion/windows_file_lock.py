"""Exclusive flock subset backed by Windows byte-range locks.

Only lock files are supported. Locks are released when the handle is closed,
including process termination. No permissions or trust settings are changed.
"""
import errno
import msvcrt
import os
import time

LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


def flock(file, operation):
    fd = file if isinstance(file, int) else file.fileno()
    os.lseek(fd, 0, os.SEEK_SET)
    if operation == LOCK_UN:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    if operation not in (LOCK_EX, LOCK_EX | LOCK_NB):
        raise ValueError("Only exclusive file locks are supported")
    if os.fstat(fd).st_size == 0:
        os.write(fd, b"\0")
    while True:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            if operation & LOCK_NB:
                raise BlockingIOError(errno.EAGAIN, "Lock is already held") from error
            time.sleep(0.05)


def replace(source, target):
    """Publish snapshots with ReplaceFileW, preserving readers of the old file.

    MoveFileEx (os.replace) cannot replace an open destination even when readers
    share delete access. Use it only for the initial, absent destination.
    https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-replacefilew
    """
    import ctypes as c
    from ctypes import wintypes as w
    replace_file = c.WinDLL('kernel32', use_last_error=True).ReplaceFileW
    replace_file.argtypes = [w.LPCWSTR,w.LPCWSTR,w.LPCWSTR,w.DWORD,w.LPVOID,w.LPVOID]
    replace_file.restype = w.BOOL
    deadline = time.monotonic() + 1.0
    while True:
        try:
            if replace_file(os.fspath(target),os.fspath(source),None,0,None,None):return
            code = c.get_last_error()
            if code == 2: return os.replace(source, target)
            raise c.WinError(code)
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
