from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def _run(code:str,tmp_path:Path,timeout:int=120)->str:
    env=os.environ.copy();env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/'data'),"WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/'auth'),"WORKFLOW_OBSERVER_CONFIG":str(tmp_path/'config.json'),"PYTHONPATH":str(ROOT)})
    result=subprocess.run([sys.executable,'-c',code],cwd=ROOT,env=env,text=True,capture_output=True,timeout=timeout)
    assert result.returncode==0,f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_new_install_has_seven_day_undecided_grace_existing_install_preserved(tmp_path):
    _run(r'''
from server.db import init_db
from server.history_retention import initialize_history_retention
init_db();p=initialize_history_retention();assert p['human_retention']=={'mode':'days','days':7};assert p['agent_retention']=={'mode':'days','days':7};assert p['onboarding_complete'] is False;assert p['ai_history_access']['mode']=='off'
''',tmp_path/'new')
    _run(r'''
from datetime import datetime, timezone
from server.db import init_db,insert_events
from server.history_retention import initialize_history_retention
init_db();insert_events([{'event_id':'old','observed_at':datetime.now(timezone.utc).isoformat(),'device_id':'d','session_id':'s','app':'Editor','event_type':'focus_span','duration_seconds':1,'metadata':{}}]);p=initialize_history_retention();assert p['human_retention']['mode']=='forever';assert p['upgrade_preserved_existing_history'] is True
''',tmp_path/'existing')


def test_v0109_undecided_ephemeral_policy_migrates_to_grace_not_ai_access(tmp_path):
    _run(r'''
import json
from shared.history_policy import policy_path,read_policy
path=policy_path();path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps({'version':1,'onboarding_complete':False,'human_retention':{'mode':'ephemeral','days':None},'agent_retention':{'mode':'ephemeral','days':None},'upgrade_preserved_existing_history':False,'history_generation':1,'session_tombstones':[],'ai_history_access':{'mode':'off'}}),encoding='utf-8')
p=read_policy();assert p['human_retention']=={'mode':'days','days':7};assert p['agent_retention']=={'mode':'days','days':7};assert p['onboarding_complete'] is False;assert p['ai_history_access']['mode']=='off'
''',tmp_path)


def test_uninitialized_legacy_runtime_does_not_delete_completed_agent_run(tmp_path):
    _run(r'''
from datetime import datetime, timedelta, timezone
from server.db import init_db,connect
from server.agent_ingest import ingest_agent_payloads
from shared.history_policy import read_policy
init_db();assert read_policy()['agent_retention']['mode']=='forever'
base=datetime.now(timezone.utc)-timedelta(minutes=2)
events=[{'observed_at':base.isoformat(),'agent_name':'LegacyAgent','provider':'test','framework':'custom','operation':'run_started','status':'running','observation_level':'native_trace','run_id':'legacy-run','session_id':'legacy-run','tool_category':'none'},{'observed_at':(base+timedelta(seconds=1)).isoformat(),'agent_name':'LegacyAgent','provider':'test','framework':'custom','operation':'run_finished','status':'success','observation_level':'native_trace','run_id':'legacy-run','session_id':'legacy-run','tool_category':'none'}]
r=ingest_agent_payloads(events);assert r['inserted']==2
with connect() as c: assert c.execute("SELECT COUNT(*) FROM events WHERE session_id='legacy-run'").fetchone()[0]==2
''',tmp_path/'legacy')


def test_undecided_grace_survives_restart_then_explicit_ephemeral_tombstones(tmp_path):
    _run(r'''
from datetime import datetime, timezone
from server.db import init_db,insert_events,connect
from server.history_retention import initialize_history_retention,cleanup_expired_history
from shared.capture_control import filter_recordable
from shared.history_policy import history_generation,update_retention
init_db();p=initialize_history_retention();assert p['onboarding_complete'] is False and p['human_retention']=={'mode':'days','days':7}
now=datetime.now(timezone.utc).isoformat();event={'event_id':'e1','observed_at':now,'device_id':'d','session_id':'grace-1','app':'Editor','event_type':'focus_span','duration_seconds':1,'metadata':{}};insert_events([event]);cleanup_expired_history(startup=True)
with connect() as c: assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==1
update_retention(human_mode='ephemeral',human_days=None,agent_mode='ephemeral',agent_days=None,onboarding_complete=True);g=history_generation();cleanup_expired_history(startup=True);assert history_generation()>g
with connect() as c: assert c.execute('SELECT COUNT(*) FROM events').fetchone()[0]==0
late=dict(event,event_id='e2');kept,suppressed=filter_recordable([late]);assert kept==[] and suppressed==1
''',tmp_path)


def test_history_sessions_activity_blocks_and_agent_observation(tmp_path):
    _run(r'''
from datetime import datetime,timedelta,timezone
from server.db import init_db,insert_events
from shared.history_policy import initialize_policy,update_retention
from server.history_retention import list_history
from server.agent_ingest import ingest_agent_payloads
init_db();initialize_policy(has_existing_evidence=False);update_retention(human_mode='days',human_days=90,agent_mode='days',agent_days=90)
base=datetime.now(timezone.utc)-timedelta(hours=3)
def h(eid,offset): return {'event_id':eid,'observed_at':(base+timedelta(minutes=offset)).isoformat(),'device_id':'d','session_id':'human-1','app':'Editor','window_title':'Work','event_type':'focus_span','duration_seconds':60,'metadata':{'activity':{'foreground_seconds':60,'engaged_seconds':50}}}
insert_events([h('h1',0),h('h2',10),h('h3',55)])
ingest_agent_payloads([{'observed_at':(base+timedelta(minutes=5)).isoformat(),'agent_name':'ChatGPT','provider':'openai','framework':'chatgpt_web','operation':'run_started','status':'running','observation_level':'os_observed','run_id':'web-test1','session_id':'web-test1','tool_category':'none'},{'observed_at':(base+timedelta(minutes=6)).isoformat(),'agent_name':'ChatGPT','provider':'openai','framework':'chatgpt_web','operation':'run_finished','status':'success','observation_level':'os_observed','run_id':'web-test1','session_id':'web-test1','tool_category':'none'}])
h=list_history(limit=20);human=next(x for x in h['sessions'] if x['kind']=='human');agent=next(x for x in h['sessions'] if x['kind']=='agent');assert len(human['activity_blocks'])==2,human;assert human['event_count']==3;assert agent['observation_level']=='os_observed';assert h['derived_task_labels_used'] is False
''',tmp_path)


def test_ai_saved_history_lease_is_explicit_range_bounded_and_expires(tmp_path):
    _run(r'''
from datetime import datetime,timedelta,timezone
from shared.history_policy import initialize_policy,set_ai_history_access,active_ai_history_access
initialize_policy(has_existing_evidence=False);assert active_ai_history_access()['mode']=='off'
start=datetime.now(timezone.utc)-timedelta(days=5);end=start+timedelta(days=2)
a=set_ai_history_access(mode='selected_range',since=start.isoformat(),until=end.isoformat(),expires_minutes=60);assert a['mode']=='selected_range';assert a['since'] and a['until'];set_ai_history_access(mode='off',expires_minutes=None);assert active_ai_history_access()['mode']=='off'
''',tmp_path)


def test_browser_agent_projection_is_structural_only(tmp_path):
    _run(r'''
from datetime import datetime,timezone
from server.main import BrowserEvent,BrowserPage,BrowserTarget
from server.browser_agent_projection import _agent_payload
b=BrowserEvent(observed_at=datetime.now(timezone.utc).isoformat(),action='agent_run_started',page=BrowserPage(hostname='chatgpt.com',pathname='/',title='ChatGPT'),target=BrowserTarget(role='agent-lifecycle',label='ChatGPT'),metadata={'agent_provider':'chatgpt','agent_run_id':'web-deadbeef','state_source':'send_control'})
p=_agent_payload(b);assert p['agent_name']=='ChatGPT';assert p['provider']=='openai';assert p['framework']=='chatgpt_web';assert p['observation_level']=='os_observed';assert p['operation']=='run_started';assert not any(k in p for k in ('prompt','response','content','tool_arguments','tool_result'))
''',tmp_path)


def test_history_architecture_is_registered_and_no_filesystem_sensor_added():
    runner=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    assert 'server.history_routes' in runner and 'server.browser_agent_projection' in runner
    adapter=(ROOT/'browser_extension'/'agent_surface_adapters.js').read_text(encoding='utf-8')
    assert 'FileSystem' not in adapter and 'FileReader' not in adapter
    policy=(ROOT/'shared'/'history_policy.py').read_text(encoding='utf-8')
    assert 'ai_history_access' in policy and 'session_tombstones' in policy and 'UNDECIDED_RETENTION_GRACE_DAYS = 7' in policy
