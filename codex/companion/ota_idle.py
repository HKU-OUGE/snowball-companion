"""Fail-closed host idle evidence for future automatic OTA orchestration."""
import math,time
MAX_SNAPSHOT_AGE=12
DEFAULT_IDLE_SECONDS=300

def idle_evidence(snapshot,now=None):
 now=time.time() if now is None else now
 if not isinstance(snapshot,dict) or snapshot.get('version')!=1 or snapshot.get('hub_alive') is not True or snapshot.get('directory_available') is not True:return False
 stamp=snapshot.get('generated_at');pending=snapshot.get('pending_total',0);sessions=snapshot.get('sessions')
 if type(stamp) not in (int,float) or not math.isfinite(stamp) or not now-MAX_SNAPSHOT_AGE<stamp<=now+5:return False
 if not isinstance(sessions,list):return False
 # A fresh directory proves presence, not activity coverage. In particular,
 # an empty/truncated list must never imply that every provider is idle.
 # Only an independent complete inventory may make that assertion; absent
 # such an authority the device must continue to defer automatic activation.
 coverage=snapshot.get('activity_coverage')
 if not isinstance(coverage,dict) or coverage.get('complete') is not True:return False
 coverage_stamp=coverage.get('observed_at');coverage_expiry=coverage.get('expires_at')
 if (type(coverage_stamp) not in (int,float) or not math.isfinite(coverage_stamp)
     or not now-(20 if coverage.get('scope')=='codex-desktop-local' else MAX_SNAPSHOT_AGE)<coverage_stamp<=now+5
     or type(coverage_expiry) not in (int,float) or not math.isfinite(coverage_expiry)
     or coverage_expiry<=now):return False
 if coverage.get('scope')=='codex-desktop-local':
  states=coverage.get('sessions')
  if not isinstance(states,dict) or coverage.get('any_active') is not False:return False
  if any(value not in ('idle','notLoaded') for value in states.values()):return False
  for row in sessions:
   if not isinstance(row,dict):return False
   if row.get('provider')!='Codex':continue
   # A separate CLI producer is outside the Desktop runtime authority.
   if row.get('source')!='codex-hooks':return False
   key='codex-hooks/'+str(row.get('session_id',''))
   if key not in states:return False
   total=row.get('pending_total');questions=row.get('questions',[])
   if type(total) is not int or total<0 or not isinstance(questions,list):return False
   if total and (len(questions)!=total or any(not isinstance(q,dict) or q.get('status')!='ignored' for q in questions)):return False
   if type(row.get('pending_input',0)) is not int or row.get('pending_input',0)!=0:return False
   # A hook received after the read can revoke its idle observation immediately.
   updated=row.get('task_updated_at')
   if type(updated) in (int,float) and updated>=coverage_stamp and row.get('phase') not in ('idle','completed'):return False
  return True
 if type(pending) is not int or pending!=0 or snapshot.get('status') not in ('ready','known'):return False
 for row in sessions:
  if not isinstance(row,dict) or row.get('phase') not in ('idle','completed'):return False
  pending=row.get('pending_input',0)
  if type(pending) is not int or pending!=0:return False
  pending_total=row.get('pending_total')
  if type(pending_total) is not int or pending_total!=0:return False
  # A completed/idle row without a recent source observation cannot prove
  # that it has not resumed while the status provider was unavailable.
  expires=row.get('expires_at')
  if row.get('source_fresh') is not True or type(expires) not in (int,float) or not math.isfinite(expires) or expires<=now:return False
 return True

class IdleClock:
 def __init__(self,seconds=DEFAULT_IDLE_SECONDS):
  if type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:raise ValueError('invalid idle duration')
  self.seconds=seconds;self.since=None
 def update(self,eligible,now=None):
  now=time.monotonic() if now is None else now
  if not eligible or not math.isfinite(now) or (self.since is not None and now<self.since):self.since=None;return False
  if self.since is None:self.since=now;return False
  return now-self.since>=self.seconds
