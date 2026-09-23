from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from shared import capture_control, evidence_deletion

ROOT = Path(__file__).resolve().parents[1]


def test_deletion_tombstone_suppresses_late_events_and_clips_duration(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(tmp_path))
    evidence_deletion.add_tombstone(
        "2026-09-23T08:05:00+00:00",
        "2026-09-23T08:06:00+00:00",
    )
    capture_control.initialize_run("2026-09-23T08:00:00+00:00")

    kept, suppressed = capture_control.filter_recordable([
        {"event_id": "inside", "observed_at": "2026-09-23T08:05:20+00:00", "duration_seconds": 0},
        {"event_id": "crossing", "observed_at": "2026-09-23T08:04:50+00:00", "duration_seconds": 30, "metadata": {}},
        {"event_id": "after", "observed_at": "2026-09-23T08:06:10+00:00", "duration_seconds": 0},
    ])
    assert suppressed == 1
    assert [row["event_id"] for row in kept] == ["crossing", "after"]
    crossing = kept[0]
    assert crossing["duration_seconds"] == 10.0
    assert crossing["metadata"]["local_deletion_clipped"] is True


def test_authenticated_delete_removes_all_local_copies_and_prevents_reappearance(tmp_path):
    code = r'''
import json
from pathlib import Path
from fastapi.testclient import TestClient

from server.db import init_db, insert_events, rows
from collector.outbox import EventOutbox
from connector.state import SyncState
from server.local_auth import ensure_api_token

DATA=Path(__import__('os').environ['WORKFLOW_OBSERVER_DATA'])
DATA.mkdir(parents=True,exist_ok=True)
(DATA/'screenshots').mkdir(parents=True,exist_ok=True)
shot=DATA/'screenshots'/'inside.jpg'; shot.write_bytes(b'test')

base={
  'schema_version':'1.0','organization_id':'','actor_id':'','device_id':'d1','sensor_id':'s1',
  'source':'desktop','session_id':'run1','app':'Editor','window_title':'Work','event_type':'screen_click',
  'duration_seconds':0,'screenshot_path':None,'metadata':{}
}
inside=dict(base,event_id='inside',observed_at='2026-09-23T08:05:20+00:00',screenshot_path=str(shot))
crossing=dict(base,event_id='crossing',observed_at='2026-09-23T08:04:50+00:00',event_type='focus_span',duration_seconds=30)
outside=dict(base,event_id='outside',observed_at='2026-09-23T08:07:00+00:00')
init_db(); assert insert_events([inside,crossing,outside])==3

with (DATA/'events.jsonl').open('w',encoding='utf-8') as handle:
    for event in (inside,crossing,outside): handle.write(json.dumps(event)+'\n')
outbox=EventOutbox(DATA/'collector_outbox.db')
for event in (inside,crossing,outside): outbox.enqueue(event)
state=SyncState(DATA/'gateway_sync_state.db'); state.set_int('last_local_event_id',1)

from server.enterprise_app import app
import server.evidence_delete_routes  # registers the additive authenticated route
headers={'Authorization':f'Bearer {ensure_api_token()}'}
with TestClient(app) as client:
    unauth=client.post('/v1/evidence/delete',json={'since':'2026-09-23T08:05:00+00:00','until':'2026-09-23T08:06:00+00:00'})
    assert unauth.status_code==401, unauth.text
    response=client.post('/v1/evidence/delete',headers=headers,json={'since':'2026-09-23T08:05:00+00:00','until':'2026-09-23T08:06:00+00:00'})
    assert response.status_code==200, response.text
    payload=response.json()
    assert payload['deleted_events']==2, payload
    assert payload['late_delivery_suppressed'] is True
    assert payload['gateway_recall_performed'] is False
    assert payload['collector_outbox_events_removed']==2
    assert payload['gateway_local_rows_marked_never_share']==2

    remaining=rows('SELECT event_id FROM events ORDER BY id')
    assert [row['event_id'] for row in remaining]==['outside'], remaining
    assert [row['event_id'] for row in rows('SELECT event_id FROM normalized_events ORDER BY id')]==['outside']
    assert [row['event_id'] for row in rows('SELECT event_id FROM context_events ORDER BY id')]==['outside']
    assert not shot.exists()
    assert [row['event_id'] for row in outbox.pending(10)]==['outside']
    assert state.skipped(1) and state.skipped(2)

    jsonl=[json.loads(line) for line in (DATA/'events.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    assert [row['event_id'] for row in jsonl]==['outside']

    # A queued/sensor event arriving after deletion cannot recreate the range.
    late=dict(base,event_id='late-inside',observed_at='2026-09-23T08:05:30+00:00')
    late_response=client.post('/v1/events',headers=headers,json={'events':[late]})
    assert late_response.status_code==200 and late_response.json()['inserted']==0, late_response.text
    assert not rows("SELECT event_id FROM events WHERE event_id='late-inside'")

audit=[json.loads(line) for line in (DATA/'evidence_deletion_audit.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
assert len(audit)==1
assert audit[0]['deleted_events']==2
assert 'event_id' not in audit[0] and 'event_ids' not in audit[0]
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


def test_delete_route_is_registered_only_as_additive_enterprise_extension():
    runner=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    route=(ROOT/'server'/'evidence_delete_routes.py').read_text(encoding='utf-8')
    assert 'import server.evidence_delete_routes' in runner
    assert '@app.post("/v1/evidence/delete")' in route
    assert 'gateway_recall_performed' in route
    assert 'add_tombstone(since, until)' in route
