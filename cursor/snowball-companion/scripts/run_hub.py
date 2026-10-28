#!/usr/bin/env python3
"""Optional foreground hub for a new installation. Never probes another AI app."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
import argparse
import fcntl
import os
import signal
import threading
from status_hub import StatusHub, HubServer, STATE_DIR, TOKEN_PATH, private_dir, load_token, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    private_dir(STATE_DIR)
    fd = os.open(STATE_DIR / 'hub.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print('已有 Snowball Hub，直接复用；不会停止或替换它。')
        return 1
    hub = StatusHub(STATE_DIR, desktop=None)
    try:
        server = HubServer(('127.0.0.1', 4190), hub, load_token(TOKEN_PATH))
    except OSError:
        os.close(fd)
        print('4190 端口已占用，未停止任何程序。')
        return 1
    stop = threading.Event()
    def tick():
        while not stop.wait(1):
            hub.publish()
    def terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    worker = threading.Thread(target=tick, daemon=True)
    worker.start()
    print('Snowball Hub 已启动：127.0.0.1:4190。此窗口需保持运行；Ctrl+C 停止。', flush=True)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set(); worker.join(timeout=2); server.server_close()
        snapshot = hub.snapshot()
        snapshot.update(hub_alive=False, source_fresh=False, status='unknown')
        snapshot.pop('state', None)
        atomic_json(hub.snapshot_path, snapshot)
        os.close(fd)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
