#!/usr/bin/env python3
"""Install the optional local speech worker and pinned multilingual model."""
from pathlib import Path
import json,os,subprocess,sys,venv,shutil

def main():
    root=Path.home()/'.cache/xueqiu';env=root/'speech-venv'
    python=env/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    packages=['faster-whisper==1.2.1','huggingface-hub>=0.25,<3','opencc-python-reimplemented==0.1.7']
    uv=shutil.which('uv')
    if uv:
        if not (env/'pyvenv.cfg').exists():subprocess.run([uv,'venv','--allow-existing','--python',sys.executable,str(env)],check=True)
        subprocess.run([uv,'pip','install','--python',str(python),*packages],check=True)
    else:
        if not python.exists():venv.EnvBuilder(with_pip=True).create(env)
        subprocess.run([str(python),'-m','pip','install',*packages],check=True)
    code='''from huggingface_hub import snapshot_download
from pathlib import Path
import json
p=snapshot_download('Systran/faster-whisper-small',revision='536b0662742c02347bc0e980a01041f333bce120',cache_dir=str(Path.home()/'.cache/xueqiu/speech-models'),allow_patterns=['model.bin','config.json','tokenizer.json','vocabulary.*','preprocessor_config.json'])
print(json.dumps({'model':p}))
'''
    result=subprocess.run([str(python),'-c',code],check=True,capture_output=True,text=True)
    model=json.loads(result.stdout.strip().splitlines()[-1])['model']
    config=Path.home()/'.config/xueqiu/speech.json';config.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    config.write_text(json.dumps({'model':model,'language':'auto','python':str(python)},indent=2)+'\n');config.chmod(0o600)
    from speech_relay import ensure_service
    ensure_service()
    print('Local speech installed. The companion will enable recording when the service is ready. No cloud API key is required.')
if __name__=='__main__':main()
