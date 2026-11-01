"""Optional per-user login services; only manage units owned by this exact install."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parent
SCRIPTS={'hub':'status_hub.py','bridge':'bridge.py'}
STATE=Path.home()/'.local/state/xueqiu'
UNITS=Path.home()/'.config/systemd/user'

def unit_name(kind):return f'snowball-{kind}.service'
def marker(kind):return f'# Snowball managed install: {ROOT / SCRIPTS[kind]}'
def owned(kind):
    if kind not in SCRIPTS:raise ValueError('Unknown shared service')
    if sys.platform=='win32':
        import windows_runtime
        # Ownership means a verified task for this installation, not simply
        # that Windows supports the manager. Unknown/foreign tasks stay false.
        try:return windows_runtime.autostart_status().get('owned') is True
        except (OSError,ValueError,RuntimeError,subprocess.SubprocessError):return False
    path=UNITS/unit_name(kind)
    try:return not path.is_symlink() and path.stat().st_uid==os.getuid() and path.read_text().splitlines()[0]==marker(kind)
    except (OSError,IndexError):return False

def control(kind,command):
    """Call only after owned(); never affect another installation's unit."""
    if sys.platform=='win32':
        if kind not in SCRIPTS or command not in ('start','stop','status'):raise ValueError('Unsupported Windows shared runtime operation')
        import windows_runtime
        result=getattr(windows_runtime,command)();print(json.dumps(result,ensure_ascii=False))
        return 0 if result.get('state') not in ('unverified','unreachable') else 2
    if not owned(kind):raise ValueError('Service is not owned by this install')
    if command=='status':
        result=subprocess.run(['systemctl','--user','is-active',unit_name(kind)],capture_output=True,text=True,timeout=10)
        print(f'Snowball {kind}: {result.stdout.strip() or "inactive"} (user login service)')
        return result.returncode
    subprocess.run(['systemctl','--user',command,unit_name(kind)],check=True,timeout=20)
    print(f'Snowball {kind} service {command} completed.')
    return 0

def quote(value):return '"'+str(value).replace('%','%%').replace('\\','\\\\').replace('"','\\"')+'"'
def contents(kind,python):
    args=[python,'-u',ROOT/SCRIPTS[kind]]
    if kind=='bridge':args+=['watch','--source',STATE/'hub/snapshot.json']
    dependencies='Wants=snowball-hub.service\nAfter=snowball-hub.service\n' if kind=='bridge' else ''
    return (marker(kind)+'\n[Unit]\nDescription=Snowball '+kind+'\n'+dependencies+
        '[Service]\nType=simple\nExecStart='+' '.join(map(quote,args))+
        '\nWorkingDirectory='+str(ROOT).replace('%','%%')+'\nRestart=on-failure\nRestartSec=3\nTimeoutStopSec=15\nUMask=0077\n'+
        'StandardOutput=append:'+str(STATE/(kind+'.log')).replace('%','%%')+'\nStandardError=inherit\n'+
        '[Install]\nWantedBy=default.target\n')

def install(python):
    if sys.platform=='win32':
        import windows_runtime
        executable=None if getattr(sys,'frozen',False) and Path(python)==Path(sys.executable) else python
        windows_runtime.install_autostart(executable=executable)
        windows_runtime.start(executable=executable)
        return
    if sys.platform!='linux':raise ValueError('Login service currently supports Linux systemd users')
    python=Path(python).absolute()
    if not python.is_file():raise ValueError('Python runtime missing')
    UNITS.mkdir(parents=True,exist_ok=True);STATE.mkdir(parents=True,exist_ok=True,mode=0o700)
    for kind in SCRIPTS:
        p=UNITS/unit_name(kind)
        if p.exists() and not owned(kind):raise ValueError('Refusing to replace another unit: '+p.name)
    # Stop legacy managed children before installing the service routing.
    for kind,script in [('bridge','manage.py'),('hub','manage_hub.py')]:
        if owned(kind):
            # A failed install can leave an unloaded/bad-setting unit with no process.
            result=subprocess.run(['systemctl','--user','show','--property=MainPID','--value',unit_name(kind)],check=True,capture_output=True,text=True,timeout=10)
            if int(result.stdout.strip())>0:control(kind,'stop')
        else:
            subprocess.run([str(python),str(ROOT/script),'stop'],check=True,timeout=20)
    for kind in SCRIPTS:
        p=UNITS/unit_name(kind);p.write_text(contents(kind,python));p.chmod(0o600)
    subprocess.run(['systemctl','--user','daemon-reload'],check=True,timeout=15)
    subprocess.run(['systemctl','--user','enable','--now',*[unit_name(k) for k in SCRIPTS]],check=True,timeout=30)
    for kind in SCRIPTS:
        subprocess.run(['systemctl','--user','is-active','--quiet',unit_name(kind)],check=True,timeout=10)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('command',choices=['install']);p.add_argument('--python',default=sys.executable)
    a=p.parse_args();install(a.python)
