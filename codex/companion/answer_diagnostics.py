"""Read-only answer-channel diagnostics, with optional explicit host handoff.

Does not send chats, answer questions, restart processes or print endpoint names,
credentials, question text, chat titles or account identifiers.
"""
import argparse
import json
import os
from pathlib import Path
import time

from app_tools import AppTools
from private_files import safe_reader
from windows_app_endpoint import candidates, remember_current_endpoint, stored_endpoint, valid, VARIABLE


def diagnose(remember=False):
    recorded = remember_current_endpoint() if remember else None
    app = AppTools()
    available = app.available()
    snapshot = None
    try:
        with safe_reader(Path.home()/'.local/state/xueqiu/hub/snapshot.json') as stream:
            raw = stream.read(8*1024*1024+1)
        if len(raw) <= 8*1024*1024:
            snapshot = json.loads(raw)
    except (OSError, ValueError):
        pass
    result = {'probe_context':'this_process', 'windows':os.name=='nt',
              'host_endpoint_present':valid(os.environ.get(VARIABLE,'')),
              'stored_endpoint_present':stored_endpoint() is not None,
              'endpoint_candidates':len(candidates()), 'handoff_recorded':recorded,
              'app_tools_available':available, 'probe_error':getattr(app,'_probe_error',''),
              'hub_snapshot_available':isinstance(snapshot,dict)}
    if isinstance(snapshot,dict):
        rows=snapshot.get('sessions',[])
        questions=[q for row in rows for q in row.get('questions',[]) if q.get('status')!='ignored']
        result.update(hub_snapshot_age_seconds=round(time.time()-snapshot.get('generated_at',0),1),
                      hub_can_create=snapshot.get('can_create',False),
                      hub_instruction_available_count=sum(bool(r.get('instruction_available')) for r in rows),
                      hub_pending_questions=len(questions),
                      hub_answerable_questions=sum(bool(q.get('answerable')) for q in questions),
                      hub_questions_with_options=sum(bool(q.get('options')) for q in questions))
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--remember-host',action='store_true',help='Pass this Codex host endpoint to the user-owned background service')
    args=parser.parse_args()
    print(json.dumps(diagnose(args.remember_host),ensure_ascii=False,indent=2))
