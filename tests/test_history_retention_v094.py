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


def test_new_install_ephemeral_existing_install_preserved(tmp_path):
    _run(r'''
from datetime import datetime, timezone
from server.db import init_db,insert_events
from server.history_retention import initialize_history_retention
from shared.history_policy import read_policy
init_db();p=initialize_history_retention();assert p['human_retention']['mode']=='ephemeral';assert p['agent_retention']['mode']=='ephemeral';assert p['onboarding_complete'] is False
''',tmp_path/'new')
    _run(r'''
from datetime import datetime, timezone
from server.db import init_db,insert_events
from server.history_retention import initialize_history_retention
init_db();insert_events([{'event_id':'old','observed_at':datetime.now(timezone.utc).isoformat(),'device_id':'d','session_id':'s','app':'Editor','event_type':'focus_span','duration_seconds':1,'metadata':{}}]);p=initialize_history_retention();assert p['human_retention']['mode']=='forever';assert p['upgrade_preserved_existing_history'] is True
''',tmp_path/'existing')


def test_ephemeral_cleanup_tombstones_late_delivery_and_bumps_generation(tmp_path):
    _run(r'''
from datetime import datetime, timezone
from server.db import init_db,insert_events,connect
from server.history_retention import initialize_history_retention,cleanup_expired_history
from shared.capture_control import filter_recordable
from shared.history_policy import history_generation
init_db();initialize_history_retention();now=datetime.now(timezone.utc).isoformat();event={'event_id':'e1','observed_at':now,'device_id':'d','session_id':'ephemeral-1','app':'Editor','event_type':'focus_span','duration_seconds':1,'metadata':{}};insert_events([event]);g=history_generation();cleanup_expired_history(startup=True);assert history_generation()>g
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
ingest_agent_payloads([{'observed_at':(base+timed(minutes=5)).isoformat(),'agent_name':'ChatGPT','provider':'openai','framework':'chatgpt_web','operation':'run_started','status':'running','observation_level':'os_observed','run_id':'web-test1','session_id':'web-test1','tool_category':'none'},{'observed_at':(base+timed(minutes=6)).isoformat(),'agent_name':'ChatGPT','provider':'openai','framework':'chatgpt_web','operation':'run_finished','status':'success','observation_level':'os_observed','run_id':'web-test1','session_id':'web-test1','tool_category':'none'}])
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
    assert 'ai_history_access' in policy and 'session_tombstones' in policy
