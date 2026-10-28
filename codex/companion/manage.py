#!/usr/bin/env python3
"""Manage this install's local BLE bridge, using its user service when installed."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
ROOT = Path.home()/'.local/state/xueqiu'
RECORD = ROOT/'bridge-runtime.json'
BRIDGE = Path(__file__).resolve().with_name('bridge.py')


def identity(pid, expected_script=None):
    # Linux PID identity, checked before signaling to avoid PID reuse.
    p=Path('/proc')/str(pid)
    try:
        cmd=(p/'cmdline').read_bytes().split(b'\0')
        start=(p/'stat').read_text().split(') ',1)[1].split()[19]
        return start if str(expected_script or BRIDGE).encode() in cmd else None
    except (OSError,IndexError):return None


def running():
    try:
        rec=json.loads(RECORD.read_text())
        return rec if identity(rec['pid'], rec.get('script', str(BRIDGE)))==rec['start'] else None
    except (OSError,ValueError,KeyError):return None


def stop(quiet=False):
    rec=running()
    if rec:
        os.kill(rec['pid'],signal.SIGTERM)
        for _ in range(30):
            if identity(rec['pid'], rec.get('script', str(BRIDGE))) is None:break
            time.sleep(.1)
        if not quiet:print('Stopped this Snowball background bridge.')
    elif not quiet:print('No managed Snowball bridge is running.')
    RECORD.unlink(missing_ok=True)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['start','stop','status'])
    p.add_argument('--source', type=Path, help='Explicit status JSON source, e.g. the local status hub snapshot')
    a=p.parse_args()
    if a.source is not None and a.command != 'start':
        p.error('--source is only valid with start')
    from user_services import owned,control
    if owned('bridge'):
        if a.source is not None and a.source.expanduser().resolve()!=ROOT/'hub/snapshot.json':
            p.error('Login service uses the live Hub source; no demo source substitution')
        raise SystemExit(control('bridge',a.command))
    if a.command=='stop':stop();return
    rec=running()
    if a.command=='status':
        print('Running' if rec else 'Stopped')
        if rec and rec.get('source'):print(f'Source: {rec["source"]}')
        return
    if rec:
        if a.source is not None and str(a.source.expanduser().resolve()) != rec.get('source'):
            p.error('Bridge already running with another source; stop it before changing --source')
        print('Snowball bridge already running.');return
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700);ROOT.chmod(0o700)
    source=a.source.expanduser().resolve() if a.source is not None else ROOT/'desktop-state.json'
    if a.source is None and not source.exists():
        fd=os.open(source,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:f.write('{"provider":"Codex","state":"idle"}\n')
    fd=os.open(ROOT/'bridge.log',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as log:
        process=subprocess.Popen([sys.executable,str(BRIDGE),'watch','--source',str(source)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    start=None
    for _ in range(20):
        start=identity(process.pid)
        if start:break
        time.sleep(.05)
    if start is None:raise RuntimeError('Background bridge failed to start')
    RECORD.write_text(json.dumps({'pid':process.pid,'start':start,'script':str(BRIDGE),'source':str(source)})+'\n');RECORD.chmod(0o600)
    print(f'Background bridge started. Source: {source}')
    if a.source is None:print('Default source is manual/demo status, not an AI application event feed.')
    else:print('Explicit source selected; missing, invalid, or expired snapshots do not refresh watch status.')
    print('This is a local process, not a boot autostart service.')


if __name__=='__main__':main()
