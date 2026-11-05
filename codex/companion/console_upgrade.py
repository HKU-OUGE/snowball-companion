"""Replace only an idle, authenticated older console process; never touches Hub/bridge."""
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from private_files import checked_path,read_text


def rpc(record, path, data=None, browser_session=None):
    headers={'Authorization':'Bearer '+record['token']}
    if browser_session:headers={'X-Snowball-UI':'1','X-Snowball-Session':browser_session}
    body=None if data is None else json.dumps(data)
    if body is not None:headers['Content-Type']='application/json'
    connection=http.client.HTTPConnection('127.0.0.1',record['port'],timeout=5)
    try:
        connection.request('GET' if data is None else 'POST',path,body,headers)
        response=connection.getresponse();raw=response.read(131073)
        if response.status!=200 or len(raw)>131072:raise RuntimeError('console_upgrade_probe_failed')
        return json.loads(raw)
    finally:connection.close()


def linux_owned(pid,script):
    try:
        folder=Path('/proc')/str(pid)
        args=(folder/'cmdline').read_bytes().split(b'\0')
        return folder.stat().st_uid==os.getuid() and os.fsencode(script) in args
    except (OSError,ValueError):return False


def stop_owned(runtime, helper_root):
    script=str(checked_path(runtime/'console_server.py'))
    if os.name=='nt':
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(helper_root/'windows-console-control.ps1'),'-Runtime',str(runtime),'-Stop'],capture_output=True,timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise RuntimeError('console_owner_unverified')
    elif sys.platform=='linux':
        matches=[int(p.name) for p in Path('/proc').iterdir() if p.name.isdigit() and linux_owned(int(p.name),script)]
        if len(matches)!=1:raise RuntimeError('console_owner_unverified')
        pid=matches[0]
        if hasattr(os,'pidfd_open') and hasattr(signal,'pidfd_send_signal'):
            fd=os.pidfd_open(pid)
            try:
                if not linux_owned(pid,script):raise RuntimeError('console_owner_unverified')
                signal.pidfd_send_signal(fd,signal.SIGTERM)
            finally:os.close(fd)
        else:
            if not linux_owned(pid,script):raise RuntimeError('console_owner_unverified')
            os.kill(pid,signal.SIGTERM)
    else:raise RuntimeError('console_restart_required')


def ensure_setup_console(state,helper_root,installer=False):
    record=json.loads(read_text(state/'server.json'))
    if (not isinstance(record,dict) or type(record.get('port')) is not int or
        not 1024<=record['port']<=65535 or not isinstance(record.get('token'),str) or
        not 32<=len(record['token'])<=128 or not isinstance(record.get('runtime'),str)):
        raise RuntimeError('console_owner_unverified')
    health=rpc(record,'/health')
    if (health.get('app')=='snowball-console' and health.get('automatic_setup') is True
        and health.get('resource_source_policy')=='sd-small-network-large-v1'
        and health.get('usb_pairing_flow')=='auto-first-classified-v1'
        and (not installer or health.get('interactive_installer')==1)):return
    if health.get('app')!='snowball-console' or health.get('version') not in ('0.5.4','0.5.5','0.5.6','0.5.7','0.5.8','0.6.0','0.6.1','0.6.2','0.6.3'):
        raise RuntimeError('console_restart_required')
    runtime=Path(record['runtime'])
    if not runtime.is_absolute() or not checked_path(runtime/'console_server.py').is_file():
        raise RuntimeError('console_owner_unverified')
    ticket=rpc(record,'/launch',{})['ticket']
    # Bootstrap also requires the UI header but no existing browser session.
    connection=http.client.HTTPConnection('127.0.0.1',record['port'],timeout=5)
    try:
        connection.request('POST','/api/bootstrap',json.dumps({'ticket':ticket}),{'Content-Type':'application/json','X-Snowball-UI':'1'})
        response=connection.getresponse();body=response.read(4097)
        if response.status!=200 or len(body)>4096:raise RuntimeError('console_upgrade_probe_failed')
        session=json.loads(body)['session']
    finally:connection.close()
    status=rpc(record,'/api/status',browser_session=session)
    if status.get('job',{} ) and status['job'].get('state')=='running':
        raise RuntimeError('console_busy_wait_for_current_action')
    if status.get('setup',{}).get('local_running'):
        raise RuntimeError('console_busy_wait_for_current_action')
    if status.get('installer',{}).get('running'):
        raise RuntimeError('console_busy_wait_for_current_action')
    stop_owned(runtime,helper_root)
    from console_launcher import active_url
    until=time.monotonic()+8
    while active_url(state) and time.monotonic()<until:time.sleep(.1)
    if active_url(state):raise RuntimeError('console_restart_required')
