from contextlib import closing
"""Read-only user-chat inventory. Presence is separate from live task evidence."""
import json
from pathlib import Path
import sqlite3
import time
import hashlib
import unicodedata

SOURCES={'cli','vscode','exec','appServer','unknown'}
INTERNAL={'ambient_suggestion_task','ambient_suggestion','review','guardian','subagent'}
def key(thread):return 'codex-hooks/'+hashlib.sha256(thread.encode()).hexdigest()[:32]
def title(value):
    s=' '.join(''.join(c for c in value if unicodedata.category(c)[0]!='C').split()) if isinstance(value,str) else ''
    s=s[:64]
    while len(s.encode())>192:s=s[:-1]
    return s or '未命名会话'

class DesktopDirectory:
    def __init__(self,home):
        self.home=Path(home);self.root=self.home/'.codex';self.rows={};self.updated=0;self.last_scan=0;self.error=None
    def refresh(self,force=False):
        if not force and time.monotonic()-self.last_scan<5:return
        self.last_scan=time.monotonic()
        try:
            candidates=sorted(self.root.glob('state_[0-9]*.sqlite'),key=lambda p:int(p.stem.split('_')[-1]),reverse=True)
            if not candidates:raise OSError('no_catalog')
            with closing(sqlite3.connect(candidates[0].as_uri()+'?mode=ro',uri=True,timeout=.1)) as db:
                db.execute('PRAGMA query_only=ON');deadline=time.monotonic()+.4
                db.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
                cols={r[1] for r in db.execute('PRAGMA table_info(threads)')}
                if not {'id','source','archived','rollout_path','cwd'}<=cols:raise ValueError('unsupported_catalog_schema')
                # `title` is an old-schema fallback only; modern title can hold
                # initial prompt text and is not the Desktop's displayed name.
                name='name' if 'name' in cols else 'title'
                extra=lambda c:f'"{c}"' if c in cols else 'NULL'
                fields=['id',name,'source','rollout_path','cwd']+[extra(c) for c in ['thread_source','agent_path','updated_at','project_id']]
                values=db.execute('SELECT '+','.join(fields)+' FROM threads WHERE archived=0 ORDER BY updated_at DESC').fetchall()
            rows={}
            for thread,name,source,rollout,cwd,thread_source,agent_path,updated,project in values:
                if not isinstance(thread,str) or source not in SOURCES or thread_source in INTERNAL or agent_path not in (None,'','/root'):continue
                identity=key(thread)
                route=self.home/'.local/state/xueqiu/codex-adapter/hooks'/(identity.split('/')[1]+'.json')
                try:
                    if route.is_file() and route.stat().st_size<512*1024 and json.loads(route.read_text()).get('internal_session'):continue
                except (OSError,ValueError):pass
                rows[identity]={'thread':thread,'display_name':title(name),'cwd':cwd,'rollout_path':rollout,'updated_at':updated or 0,'project_id':project}
            self.rows=rows;self.updated=time.time();self.error=None
        except (OSError,ValueError,sqlite3.Error):self.error='desktop_catalog_unavailable'
    def fresh(self):return self.error is None and 0<=time.time()-self.updated<20
    def resolve(self,identity):
        self.refresh(force=True)
        return self.rows.get(identity) if self.fresh() else None
