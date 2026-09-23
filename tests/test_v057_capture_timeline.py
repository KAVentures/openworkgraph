from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from shared import capture_control

ROOT = Path(__file__).resolve().parents[1]


def test_capture_intervals_drop_paused_events_and_clip_crossing_spans(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    start = "2026-09-23T08:00:00+00:00"
    capture_control.initialize_run(start)
    capture_control.set_state("pause", at="2026-09-23T08:00:10+00:00")

    kept, suppressed = capture_control.filter_recordable([
        {"event_id": "before", "observed_at": "2026-09-23T08:00:05+00:00", "duration_seconds": 0},
        {"event_id": "during", "observed_at": "2026-09-23T08:00:11+00:00", "duration_seconds": 0},
        {"event_id": "crossing", "observed_at": "2026-09-23T08:00:09+00:00", "duration_seconds": 5, "metadata": {}},
    ])
    assert suppressed == 1
    assert [row["event_id"] for row in kept] == ["before", "crossing"]
    crossing = next(row for row in kept if row["event_id"] == "crossing")
    assert crossing["duration_seconds"] == 1.0
    assert crossing["metadata"]["capture_control_clipped"] is True

    capture_control.set_state("resume", at="2026-09-23T08:00:20+00:00")
    kept2, suppressed2 = capture_control.filter_recordable([
        {"event_id": "late-paused", "observed_at": "2026-09-23T08:00:15+00:00", "duration_seconds": 0},
        {"event_id": "after", "observed_at": "2026-09-23T08:00:21+00:00", "duration_seconds": 0},
    ])
    assert suppressed2 == 1
    assert [row["event_id"] for row in kept2] == ["after"]


def test_stop_and_start_new_run_keep_old_stop_interval_tombstoned(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    capture_control.initialize_run("2026-09-23T08:00:00+00:00")
    stopped = capture_control.set_state("stop", at="2026-09-23T08:05:00+00:00")
    assert stopped["state"] == "stopped"
    started = capture_control.set_state("start", at="2026-09-23T08:10:00+00:00")
    assert started["state"] == "recording"
    assert started["run_started_at"] == "2026-09-23T08:10:00+00:00"
    assert started["generation"] > stopped["generation"]
    assert capture_control.timestamp_is_skipped("2026-09-23T08:07:00+00:00", state=started)
    assert not capture_control.timestamp_is_skipped("2026-09-23T08:11:00+00:00", state=started)


def test_authenticated_local_capture_routes_suppress_paused_and_late_browser_evidence(tmp_path):
    code = r'''
import os
from fastapi.testclient import TestClient
from server.local_auth import ensure_api_token
from server.enterprise_app import app

headers={"Authorization": f"Bearer {ensure_api_token()}"}
base={
  "schema_version":"1.0","organization_id":"","actor_id":"","device_id":"test-device",
  "sensor_id":"test-desktop","source":"desktop","session_id":"s1","app":"Editor",
  "window_title":"Work","event_type":"screen_click","duration_seconds":0,"metadata":{}
}
with TestClient(app) as client:
    unauth=client.get('/v1/capture/status')
    assert unauth.status_code == 401, unauth.text
    status=client.get('/v1/capture/status',headers=headers)
    assert status.status_code == 200 and status.json()['state']=='recording', status.text

    first=dict(base,event_id='before',observed_at='2026-09-23T08:00:01+00:00')
    r=client.post('/v1/events',headers=headers,json={'events':[first]})
    assert r.status_code==200 and r.json()['inserted']==1, r.text

    paused=client.post('/v1/capture/pause',headers=headers)
    assert paused.status_code==200 and paused.json()['state']=='paused', paused.text
    pause_at=paused.json()['state_changed_at']

    from datetime import datetime, timedelta
    p=datetime.fromisoformat(pause_at.replace('Z','+00:00'))
    during=(p+timedelta(seconds=1)).isoformat()
    desktop=dict(base,event_id='paused-desktop',observed_at=during)
    r2=client.post('/v1/events',headers=headers,json={'events':[desktop]})
    assert r2.status_code==200 and r2.json()['inserted']==0, r2.text

    browser={
      'event_id':'paused-browser','observed_at':during,'sensor_id':'browser:test','sensor_version':'',
      'browser_session_id':'b1','work_session_id':'s1','action':'click',
      'page':{'hostname':'example.test','pathname':'/work','title':'Work'},
      'target':{'tag':'button','role':'button','label':'Save'},'metadata':{}
    }
    r3=client.post('/v1/browser-events',headers=headers,json=browser)
    assert r3.status_code==200 and r3.json()['inserted']==0, r3.text

    resumed=client.post('/v1/capture/resume',headers=headers)
    assert resumed.status_code==200 and resumed.json()['state']=='recording', resumed.text
    # A browser event from the old paused timestamp stays suppressed even after resume.
    r4=client.post('/v1/browser-events',headers=headers,json={**browser,'event_id':'late-browser'})
    assert r4.status_code==200 and r4.json()['inserted']==0, r4.text

    stopped=client.post('/v1/capture/stop',headers=headers)
    assert stopped.status_code==200 and stopped.json()['state']=='stopped', stopped.text
    restarted=client.post('/v1/capture/start',headers=headers)
    assert restarted.status_code==200 and restarted.json()['state']=='recording', restarted.text
    assert restarted.json()['generation'] > status.json()['generation']
'''
    env=os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "observe",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
    })
    result=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,text=True,capture_output=True,timeout=45)
    assert result.returncode==0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_demo_timeline_and_pattern_endpoints_reuse_existing_inference(tmp_path):
    code = r'''
import os
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from demo_data import build_demo_events
from server.db import init_db, insert_events

init_db()
insert_events(build_demo_events(datetime(2026,9,23,8,0,tzinfo=timezone.utc)))
from server.local_auth import ensure_api_token
from server.enterprise_app import app
headers={"Authorization": f"Bearer {ensure_api_token()}"}
with TestClient(app) as client:
    assert client.get('/v1/timeline?scope=all').status_code == 401
    timeline=client.get('/v1/timeline?scope=all',headers=headers)
    assert timeline.status_code==200, timeline.text
    data=timeline.json()
    assert data['temporal_source']=='focus_spans'
    assert data['browser_context_role']=='surface_attribution_only'
    assert data['double_count_browser_events'] is False
    assert data['spans']
    assert all({'surface','start','end','engaged_seconds'} <= set(row) for row in data['spans'])

    patterns=client.get('/v1/patterns?scope=all',headers=headers)
    assert patterns.status_code==200, patterns.text
    payload=patterns.json()
    assert payload['derived_task_inference_authoritative'] is False
    assert payload['needs_review'] is True
    assert payload['patterns']
    first=payload['patterns'][0]
    assert first['name']
    assert first['needs_review'] is True
    assert first['runs']
    assert 'event_ids' in first['runs'][0]
'''
    env=os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_MODE": "demo",
        "WORKFLOW_OBSERVER_RUN_STARTED_AT": "2026-09-23T08:00:00+00:00",
    })
    result=subprocess.run([sys.executable,"-c",code],cwd=ROOT,env=env,text=True,capture_output=True,timeout=45)
    assert result.returncode==0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_secure_capture_supervisor_keeps_launcher_contract_and_uses_controlled_worker():
    start=(ROOT/'start.py').read_text(encoding='utf-8')
    supervisor=(ROOT/'collector'/'secure_main.py').read_text(encoding='utf-8')
    worker=(ROOT/'collector'/'secure_worker.py').read_text(encoding='utf-8')
    assert '"-m", "collector.secure_main"' in start
    assert 'collector.secure_worker' in supervisor
    assert 'read_state()' in supervisor
    assert 'prepare_recordable_event' in worker
    assert 'ActivityTracker.record = _controlled_activity_record' in worker
