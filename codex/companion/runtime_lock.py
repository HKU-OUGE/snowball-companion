"""One user's shared transport instance, on Windows and POSIX."""
from pathlib import Path
import sys
from private_files import private_dir, private_file


def acquire(name, state=None):
    if name not in ('bridge',):
        raise ValueError('Unknown runtime lock')
    directory = private_dir(state or Path.home() / '.local/state/xueqiu')
    path = directory / (name + '-instance.lock')
    handle = path.open('a+b')
    try:
        private_file(path)
        if sys.platform == 'win32':
            import windows_file_lock as lock
        else:
            import fcntl as lock
        lock.flock(handle, lock.LOCK_EX | lock.LOCK_NB)
        return handle
    except BlockingIOError:
        handle.close()
        return None
    except BaseException:
        handle.close()
        raise
