"""Optional local-only speech worker. No cloud uploads, recording or message sends."""
import argparse,base64,hmac,io,json,os,threading,time,wave
from array import array
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import numpy as np

RATE=16000
MAX_SAMPLES=RATE*20
STEPS=(7,8,9,10,11,12,13,14,16,17,19,21,23,25,28,31,34,37,41,45,50,55,60,66,73,80,88,97,107,118,130,143,157,173,190,209,230,253,279,307,337,371,408,449,494,544,598,658,724,796,876,963,1060,1166,1282,1411,1552,1707,1878,2066,2272,2499,2749,3024,3327,3660,4026,4428,4871,5358,5894,6484,7132,7845,8630,9493,10442,11487,12635,13899,15289,16818,18500,20350,22385,24623,27086,29794,32767)
CHANGES=(-1,-1,-1,-1,2,4,6,8)
PUBLIC_ERRORS={'invalid_audio','audio_too_large','unsupported_audio','no_speech','speech_busy','speech_too_long'}

def diagnostic(event,**fields):
    # Numeric audio quality and stage timings only. Never audio or transcript.
    print(json.dumps({'time':time.time(),'speech':event,**fields},separators=(',',':')),flush=True)

def adpcm_decode(data,samples):
    if type(samples) is not int or not 8000<=samples<=MAX_SAMPLES or len(data)*2!=samples:raise ValueError('invalid_audio')
    out=array('h');predictor=index=0
    for byte in data:
        for code in (byte>>4,byte&15):
            step=STEPS[index];delta=(step>>3)+(step if code&4 else 0)+(step>>1 if code&2 else 0)+(step>>2 if code&1 else 0)
            predictor=max(-32768,min(32767,predictor+(-delta if code&8 else delta)))
            index=max(0,min(88,index+CHANGES[code&7]));out.append(predictor)
    return np.array(out,dtype=np.float32)/32768

def decode(request):
    if not isinstance(request,dict) or set(request)!={'encoding','audio','samples'}:raise ValueError('invalid_audio')
    if not isinstance(request['audio'],str) or len(request['audio'])>900000:raise ValueError('audio_too_large')
    data=base64.b64decode(request['audio'],validate=True)
    if request['encoding']=='ima-adpcm':return adpcm_decode(data,request['samples'])
    if request['encoding']!='wav':raise ValueError('unsupported_audio')
    with wave.open(io.BytesIO(data),'rb') as w:
        if w.getnchannels()!=1 or w.getsampwidth()!=2 or w.getframerate()!=RATE or not 8000<=w.getnframes()<=MAX_SAMPLES or w.getnframes()!=request['samples']:raise ValueError('invalid_audio')
        raw=w.readframes(MAX_SAMPLES+1)
        if len(raw)!=request['samples']*2:raise ValueError('invalid_audio')
    return np.frombuffer(raw,dtype='<i2').astype(np.float32)/32768

class Recognizer:
    def __init__(self,model,language='auto'):
        from faster_whisper import WhisperModel
        from opencc import OpenCC
        self.simplify=OpenCC('t2s')
        self.model=WhisperModel(str(model),device='cpu',compute_type='int8',cpu_threads=min(8,os.cpu_count() or 2),num_workers=1,local_files_only=True)
        self.lock=threading.Lock();self.language=language
    def transcribe(self,request):
        audio=decode(request)
        started=time.monotonic()
        rms=float(np.sqrt(np.mean(audio*audio)))
        diagnostic('audio_received',seconds=round(len(audio)/RATE,3),rms=round(rms,6),
            peak=round(float(np.max(np.abs(audio))),6),dc=round(float(np.mean(audio)),6))
        if rms<.0015:raise ValueError('no_speech')
        if not self.lock.acquire(blocking=False):raise ValueError('speech_busy')
        try:
            segments,info=self.model.transcribe(audio,language=None if self.language=='auto' else self.language,
                beam_size=3,condition_on_previous_text=False,vad_filter=True,
                vad_parameters={'min_silence_duration_ms':450},no_speech_threshold=.6)
            text=' '.join(s.text.strip() for s in segments if s.no_speech_prob<.7).strip()
            text=self.simplify.convert(' '.join(text.split()))
            if not text:raise ValueError('no_speech')
            if len(text)>256 or len(text.encode())>768:raise ValueError('speech_too_long')
            diagnostic('transcribed',elapsed=round(time.monotonic()-started,3),characters=len(text),
                language=info.language,speech_seconds=round(info.duration_after_vad,3))
            return {'text':text,'language':info.language,'duration':len(audio)/RATE,'engine':'faster-whisper-small-local'}
        finally:self.lock.release()

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass # Never log audio, transcripts or Authorization.
    def reply(self,status,body):
        raw=json.dumps(body,ensure_ascii=False).encode();self.send_response(status)
        self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def authorized(self):
        return (self.client_address[0]=='127.0.0.1' and self.headers.get('Host') in ('127.0.0.1:4191','localhost:4191') and
                hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.server.token))
    def do_GET(self):
        if not self.authorized():return self.reply(403,{'error':'local_only'})
        if self.path!='/v1/health':return self.reply(404,{'error':'not_found'})
        self.reply(200,{'ready':True,'engine':'faster-whisper-small-local','max_seconds':20})
    def do_POST(self):
        if not self.authorized():return self.reply(403,{'error':'local_only'})
        if self.path!='/v1/transcribe':return self.reply(404,{'error':'not_found'})
        try:
            lengths=self.headers.get_all('Content-Length',[])
            if self.headers.get('Transfer-Encoding') or len(lengths)!=1 or not lengths[0].isdigit():raise ValueError('invalid_audio')
            length=int(lengths[0])
            if not 1<=length<=910000 or self.headers.get('Content-Type')!='application/json':raise ValueError('invalid_audio')
            self.connection.settimeout(65)
            raw=self.rfile.read(length)
            if len(raw)!=length:raise ValueError('invalid_audio')
            answer=self.server.recognizer.transcribe(json.loads(raw));self.reply(200,answer)
        except (ValueError,TypeError,KeyError,wave.Error) as error:
            reason=str(error) if str(error) in PUBLIC_ERRORS else 'invalid_audio'
            diagnostic('rejected',reason=reason)
            self.reply(409 if reason=='speech_busy' else 422,{'error':reason})
        except Exception as error:
            diagnostic('failed',kind=type(error).__name__)
            self.reply(503,{'error':'speech_unavailable'})

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,default=Path.home()/'.config/xueqiu/speech.json');a=p.parse_args()
    config=json.loads(a.config.read_text());model=Path(config['model']).expanduser()
    if not (model/'model.bin').is_file():raise SystemExit('Local speech model missing; run setup-speech.py.')
    recognizer=Recognizer(model,config.get('language','auto'))
    token=(Path.home()/'.config/xueqiu/hub-token').read_text().strip()
    server=ThreadingHTTPServer(('127.0.0.1',4191),Handler);server.token=token;server.recognizer=recognizer
    print('Snowball local speech ready; audio stays on this computer.',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
if __name__=='__main__':main()
