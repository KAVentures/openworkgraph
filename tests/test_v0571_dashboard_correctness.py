from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_child(code: str, tmp_path: Path) -> None:
    env = os.environ.copy()
    env.update(
        {
            "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
            "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
            "WORKFLOW_OBSERVER_MODE": "observe",
            "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
        }
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=45,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_v0571_correctness_helpers_are_isolated(tmp_path):
    code = r'''
from types import SimpleNamespace
import server.v0571_polish as polish

def task(session,start,end):
    return {
        'task_id':f'task-{session}','session_id':session,'started_at':start,'ended_at':end,
        'surfaces':['Gmail','Salesforce','Google Sheets','Gmail'],
        'semantic_actions':['Open email','Open account','Update status','Send'],
        'engaged_seconds':60.0,'elapsed_seconds':90.0,
        'boundary':{'end_reason':'explicit_completion'},'outcomes':[],'anchor_event_ids':[],
        'task_family':'customer.followup','completion_observed':True,
    }

def factual(session,hour):
    values=[('Gmail','Open email'),('Salesforce','Open account'),('Google Sheets','Update status'),('Gmail','Send')]
    rows=[]
    for index,(surface,action) in enumerate(values):
        minute=index*2
        rows.append({
            'session_id':session,
            'started_at':f'2026-09-23T{hour:02d}:{minute:02d}:00+00:00',
            'ended_at':f'2026-09-23T{hour:02d}:{minute+1:02d}:00+00:00',
            'work_surface':surface,'semantic_actions':['page_view',action],
            'evidence_event_ids':[f'{session}-{index}'],
        })
    return rows

tasks=[task('s1','2026-09-23T08:00:00+00:00','2026-09-23T08:07:00+00:00'),task('s2','2026-09-23T09:00:00+00:00','2026-09-23T09:07:00+00:00')]
derived={
    'tasks':tasks,
    'patterns':[{
        'signature':'customer.followup','task_family':'customer.followup','observed_count':2,
        'suggested_label':'Compose and send email','surfaces':['Gmail','Salesforce','Google Sheets','Gmail'],
        'action_skeleton':['send'],'confidence':'medium','needs_review':True,
    }],
    'inference':{},
}
payload=polish._build_patterns_payload('all',derived,[*factual('s1',8),*factual('s2',9)],None)
pattern=payload['patterns'][0]
assert pattern['steps']==[
    {'surface':'Gmail','action':'Open email'},
    {'surface':'Salesforce','action':'Open account'},
    {'surface':'Google Sheets','action':'Update status'},
    {'surface':'Gmail','action':'Send'},
]
assert pattern['steps'][0]['action'].lower()!='send'
assert pattern['ends_with']=='Send'
assert pattern['step_source']=='ordered_factual_context'
assert payload['inference']['surface_action_arrays_zipped_positionally'] is False

fallback={'session_id':'none','started_at':'2026-09-23T08:00:00+00:00','ended_at':'2026-09-23T08:10:00+00:00','surfaces':['Gmail','Salesforce','Gmail']}
assert polish._run_steps(fallback,[])==[
    {'surface':'Gmail','action':''},{'surface':'Salesforce','action':''},{'surface':'Gmail','action':''},
]

paused=polish._capture_markers({'state':'paused','state_changed_at':'2026-09-23T08:05:00+00:00'})
assert paused['paused_at']=='2026-09-23T08:05:00+00:00' and paused['stopped_at'] is None
stopped=polish._capture_markers({'state':'stopped','state_changed_at':'2026-09-23T08:10:00+00:00'})
assert stopped['paused_at'] is None and stopped['stopped_at']=='2026-09-23T08:10:00+00:00'

settings=SimpleNamespace(enabled=False,url='',verify_tls=True,local_policy={
    'share_window_titles':False,'share_metadata':True,
    'allowed_event_types':['focus_span','browser_action'],'strip_metadata_keys':['internal_note'],
})
polish.load_gateway_settings=lambda *_a,**_k:settings
polish.load_device_token=lambda _settings:''
policy=polish.sharing_policy_snapshot()
assert policy['connected'] is False and policy['policy_current'] is True
assert policy['effective_policy']['share_window_titles'] is False
assert policy['effective_policy']['allowed_event_types']==['browser_action','focus_span']
assert 'typed_text' in policy['never_shared'] and 'token' not in policy
'''
    _run_child(code, tmp_path)


def test_new_policy_route_remains_behind_secure_dashboard_auth(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
import server.enterprise_app
import server.evidence_delete_routes
import server.v0571_polish
from server.secure_app import app
with TestClient(app) as client:
    response=client.get('/v1/sharing-policy')
    assert response.status_code==401, response.text
'''
    _run_child(code, tmp_path)


def test_enterprise_runner_registers_correctness_layer():
    runner = (ROOT / "server" / "enterprise_runner.py").read_text(encoding="utf-8")
    assert "import server.v0571_polish" in runner
