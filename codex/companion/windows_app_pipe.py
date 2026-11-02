"""Bounded byte-stream transport to the current user's installed Codex app.

Only the host-injected endpoint is used. No pipe enumeration or credential reads.
The connected server must belong to the current user and the Store installation.
"""
import os
import re
import time
from pathlib import Path


def endpoint():
    value = os.environ.get('CODEX_APP_TOOLS_PIPE_PATH', '')
    if re.fullmatch(r'\\\\\.\\pipe\\codex-app-tools-[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', value):
        return value
    return None


def verify_server(handle):
    import ctypes as c
    from ctypes import wintypes as w
    import _winapi
    kernel = c.WinDLL('kernel32', use_last_error=True)
    adv = c.WinDLL('advapi32', use_last_error=True)
    kernel.GetNamedPipeServerProcessId.argtypes = [w.HANDLE, c.POINTER(w.ULONG)]
    kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, c.POINTER(w.DWORD)]
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD)]
    adv.EqualSid.argtypes = [w.LPVOID, w.LPVOID]
    pid = w.ULONG()
    if not kernel.GetNamedPipeServerProcessId(handle, c.byref(pid)):
        raise OSError('Cannot verify app pipe server')
    process = _winapi.OpenProcess(0x1000, False, pid.value)
    def user(process_handle):
        token = w.HANDLE()
        if not adv.OpenProcessToken(process_handle, 8, c.byref(token)):
            raise OSError('Cannot verify app process owner')
        try:
            size = w.DWORD()
            adv.GetTokenInformation(token, 1, None, 0, c.byref(size))
            data = c.create_string_buffer(size.value)
            if not adv.GetTokenInformation(token, 1, data, size, c.byref(size)):
                raise OSError('Cannot read app process owner')
            return data
        finally:
            _winapi.CloseHandle(token.value)
    try:
        actual, current = user(process), user(_winapi.GetCurrentProcess())
        if not adv.EqualSid(c.cast(actual, c.POINTER(w.LPVOID))[0], c.cast(current, c.POINTER(w.LPVOID))[0]):
            raise OSError('App pipe belongs to another user')
        image = c.create_unicode_buffer(32768)
        size = w.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(process, 0, image, c.byref(size)):
            raise OSError('Cannot verify app installation')
        path = Path(image.value)
        root = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WindowsApps'
        relative = path.relative_to(root)
        if (len(relative.parts) != 3 or relative.parts[1:] != ('app', 'ChatGPT.exe') or
                not re.fullmatch(r'OpenAI\.Codex_[\w.]+__2p2nqsd0c76g0', relative.parts[0])):
            raise OSError('Untrusted app installation')
    except ValueError as exc:
        raise OSError('Untrusted app installation') from exc
    finally:
        _winapi.CloseHandle(process)


class WindowsAppPipe:
    def __init__(self, path, timeout=20):
        import _winapi
        if path != endpoint():
            raise OSError('App endpoint was not supplied by this host')
        self.handle = _winapi.CreateFile(path, 0xC0000000, 0, 0, 3, 0x40000000, 0)
        self.deadline = time.monotonic() + timeout
        try:
            verify_server(self.handle)
        except BaseException:
            self.close()
            raise

    def close(self):
        import _winapi
        if self.handle is not None:
            _winapi.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self): return self
    def __exit__(self, *_): self.close()

    def complete(self, operation):
        import _winapi
        remaining = max(0, int((self.deadline-time.monotonic())*1000))
        if _winapi.WaitForSingleObject(operation.event, remaining) != 0:
            operation.cancel()
            operation.GetOverlappedResult(True)
            raise TimeoutError('App pipe timed out')
        count, error = operation.GetOverlappedResult(False)
        if error: raise OSError(error, 'App pipe I/O failed')
        return count

    def sendall(self, data):
        import _winapi
        while data:
            operation, _ = _winapi.WriteFile(self.handle, data, overlapped=True)
            count = self.complete(operation)
            if not count: raise ConnectionError('App pipe closed')
            data = data[count:]

    def recv(self, size):
        import _winapi
        operation, _ = _winapi.ReadFile(self.handle, size, overlapped=True)
        self.complete(operation)
        return operation.getbuffer()

    def settimeout(self, timeout):
        self.deadline = time.monotonic() + timeout
