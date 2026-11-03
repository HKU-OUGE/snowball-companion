#!/usr/bin/env python3
"""Install a pinned local model. Existing configuration is retained unless a model is selected."""
from pathlib import Path
import argparse,json,os,subprocess,sys,venv,shutil
from private_files import private_write,read_text
from speech_profiles import PROFILES,profile_for


def progress(stage):
    print('XQSETUP '+json.dumps({'stage':stage}),flush=True)


def valid_config(value):
    return (isinstance(value,dict) and Path(value.get('python','')).is_file() and
            all((Path(value.get('model',''))/name).is_file() for name in ('model.bin','config.json','tokenizer.json')))

def activate(config,old,updated):
    """Publish only a validated model; reconcile ambiguous reload responses."""
    from speech_relay import ensure_service,local_http,SpeechServiceError
    import time
    try:health=local_http('/v1/health',timeout=1)
    except (OSError,ValueError):health={}
    # A legacy worker has no authenticated reload/busy protocol. Do not kill it
    # or change its config behind an in-flight recording.
    if health.get('ready') and not health.get('reload_supported'):
        if valid_config(old) and profile_for(old)==profile_for(updated):return
        raise RuntimeError('speech_worker_upgrade_required')
    if health.get('busy'):raise RuntimeError('speech_busy')
    private_write(config,(json.dumps(updated,indent=2)+'\n').encode('utf-8'))
    if not health:
        ensure_service();return
    try:local_http('/v1/reload',{'reload':True},timeout=120)
    except Exception as error:
        # An HTTP timeout may occur after activation. Read back the actual
        # loaded profile before deciding whether the old config can be restored.
        until=time.monotonic()+15
        current={}
        while time.monotonic()<until:
            try:current=local_http('/v1/health',timeout=1)
            except (OSError,ValueError):current={}
            if current.get('ready'):break
            time.sleep(.5)
        if current.get('ready') and current.get('profile')==profile_for(updated):return
        if (isinstance(error,SpeechServiceError) and error.status in (409,503)) or current.get('ready'):
            if old:private_write(config,(json.dumps(old,indent=2)+'\n').encode('utf-8'))
        raise RuntimeError('speech_busy' if isinstance(error,SpeechServiceError) and error.status==409 else 'speech_not_ready') from None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',choices=('auto',*PROFILES),default='auto')
    args=parser.parse_args()
    config=Path.home()/'.config/xueqiu/speech.json'
    try:old=json.loads(read_text(config))
    except (OSError,ValueError):old={}
    if not isinstance(old,dict):old={}
    if valid_config(old) and (args.model=='auto' or profile_for(old)==args.model):
        progress('speech_starting')
        activate(config,old,old)
        return
    selected='base' if args.model=='auto' else args.model
    profile=PROFILES[selected]
    root=Path.home()/'.cache/xueqiu';env=root/'speech-venv'
    python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    packages=['faster-whisper==1.2.1','huggingface-hub>=0.25,<3','opencc-python-reimplemented==0.1.7','socksio==1.0.0']
    progress('speech_dependencies')
    uv=shutil.which('uv')
    if uv:
        if not (env/'pyvenv.cfg').exists():subprocess.run([uv,'venv','--allow-existing','--python',sys.executable,str(env)],check=True)
        subprocess.run([uv,'pip','install','--python',str(python),*packages],check=True)
    else:
        if not python.exists():venv.EnvBuilder(with_pip=True).create(env)
        subprocess.run([str(python),'-m','pip','install',*packages],check=True)
    progress('speech_model')
    code='''from huggingface_hub import snapshot_download
from pathlib import Path
import json,sys
p=snapshot_download(sys.argv[1],revision=sys.argv[2],cache_dir=str(Path.home()/'.cache/xueqiu/speech-models'),allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.*','preprocessor_config.json'],token=False)
from faster_whisper import WhisperModel
m=WhisperModel(p,device='cpu',compute_type='int8',num_workers=1,local_files_only=True)
print(json.dumps({'model':p}))
'''
    from speech_offline import import_available
    model=import_available(selected)
    if model:
        progress('speech_sd_imported')
        subprocess.run([str(python),'-c',"from faster_whisper import WhisperModel;import sys;WhisperModel(sys.argv[1],device='cpu',compute_type='int8',num_workers=1,local_files_only=True)",str(model)],check=True)
    else:
        result=subprocess.run([str(python),'-c',code,profile['repo'],profile['revision']],check=True,capture_output=True,text=True)
        model=json.loads(result.stdout.strip().splitlines()[-1])['model']
    updated={'model':str(model),'language':old.get('language','auto'),'python':str(python),'profile':selected}
    progress('speech_starting')
    activate(config,old,updated)
    print('Local speech model prepared. Readiness is verified separately.',flush=True)

if __name__=='__main__':
    from runtime_lock import acquire
    lock=acquire('speech-setup')
    if lock is None:raise SystemExit('speech_install_running')
    try:main()
    finally:lock.close()
