import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'snowball-companion/runtime'))
from ota_idle import idle_evidence,IdleClock

def test_idle_evidence_rejects_stale_busy_unknown_and_unanswered():
 now=1000;s={'version':1,'hub_alive':True,'directory_available':True,'status':'ready','generated_at':now,'pending_total':0,'activity_coverage':{'complete':True,'observed_at':now,'expires_at':1010},'sessions':[{'phase':'idle','pending_input':0,'pending_total':0,'source_fresh':True,'expires_at':1010}]}
 assert idle_evidence(s,now)
 for changes in ({'hub_alive':False},{'directory_available':False},{'status':'unknown'},{'pending_total':1},{'generated_at':900},{'sessions':None}):assert not idle_evidence(dict(s,**changes),now)
 for coverage in (None,{}, {'complete':False,'observed_at':now,'expires_at':1010}, {'complete':True,'observed_at':900,'expires_at':1010}, {'complete':True,'observed_at':now,'expires_at':999}):
  assert not idle_evidence(dict(s,activity_coverage=coverage,sessions=[]),now)
 for changes in ({'phase':'running'},{'phase':'waiting'},{'phase':'failed'},{'source_fresh':False},{'expires_at':999},{'pending_input':1},{'pending_total':1}):
  assert not idle_evidence(dict(s,sessions=[dict(s['sessions'][0],**changes)]),now)

def test_activity_or_clock_regression_requires_entire_idle_interval():
 c=IdleClock(600);assert not c.update(True,1);assert not c.update(True,600);assert c.update(True,601)
 assert not c.update(False,602);assert not c.update(True,603);assert not c.update(True,602);assert not c.update(True,603);assert c.update(True,1203)
