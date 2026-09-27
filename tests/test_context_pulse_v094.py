from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(code: str, tmp_path: Path, timeout: int = 120) -> str:
    env = os.environ.copy()
    env.update({
        "WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
        "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
        "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json"),
        "PYTHONPATH": str(ROOT),
    })
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


def test_pulse_bootstrap_findings_cursor_and_late_backdated_event(tmp_path):
    _run(r'''
import base64, json
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events
from server.context_pulse import context_pulse

init_db()
now=datetime.now(timezone.utc)
events=[]
for day in range(3):
    base=now-timedelta(days=day, hours=1)
    for index,(app,offset) in enumerate([('Gmail',0),('ChatGPT',120),('Gmail',240)]):
        events.append({
            'event_id':f'base-{day}-{index}',
            'observed_at':(base+timedelta(seconds=offset)).isoformat(),
            'device_id':'d','session_id':f's{day}','app':app,
            'window_title':f'{app} work','event_type':'focus_span','duration_seconds':60,
            'metadata':{'activity':{'foreground_seconds':60,'engaged_seconds':50,'active_input_seconds':20}},
        })
assert insert_events(events)==9

first=context_pulse(recent_limit=2, finding_limit=20, lookback_days=30)
assert first['bootstrap'] is True
assert first['recent_mode']=='bootstrap_tail'
assert first['recent_returned']==2
assert first['recent_has_more'] is False
assert any(x['finding_kind']=='repeated_surface_transition' and x['status']=='baseline' for x in first['findings']), first
assert any(x['finding_kind']=='surface_engagement' and x['status']=='baseline' for x in first['findings']), first
assert all(x['factual_aggregate'] and not x['task_inference_used'] and not x['advice'] for x in first['findings'])

# Cursor carries only watermarks and opaque finding IDs/versions, never captured labels.
raw=base64.urlsafe_b64decode(first['next_cursor']+'='*(-len(first['next_cursor'])%4)).decode()
assert 'Gmail' not in raw and 'ChatGPT' not in raw and 'work' not in raw, raw

# Arrives later in storage but has an old observed_at: an arrival-id watermark must still return it.
late={
    'event_id':'late-backdated','observed_at':(now-timedelta(days=10)).isoformat(),
    'device_id':'d','session_id':'late','app':'Notes','window_title':'Late note',
    'event_type':'focus_span','duration_seconds':5,'metadata':{'activity':{'engaged_seconds':5}},
}
assert insert_events([late])==1
second=context_pulse(cursor=first['next_cursor'], recent_limit=10, lookback_days=30)
assert second['bootstrap'] is False
assert [x['event_id'] for x in second['recent_evidence']]==['late-backdated'], second

third=context_pulse(cursor=second['next_cursor'], recent_limit=10, lookback_days=30)
assert third['recent_evidence']==[]
assert third['findings']==[], third
''', tmp_path)


def test_pulse_freezes_multi_page_snapshot_and_does_not_skip_new_arrivals(tmp_path):
    _run(r'''
from datetime import datetime, timedelta, timezone
from server.db import init_db, insert_events
from server.context_pulse import context_pulse

init_db()
now=datetime.now(timezone.utc)
def event(eid, seconds):
    return {
        'event_id':eid,'observed_at':(now+timedelta(seconds=seconds)).isoformat(),
        'device_id':'d','session_id':'s','app':'Editor','window_title':'Editor',
        'event_type':'ui_click','duration_seconds':0,'metadata':{'action':'click'},
    }

assert insert_events([event('seed',0)])==1
baseline=context_pulse(recent_limit=10)
assert insert_events([event('a',1),event('b',2),event('c',3)])==3
page1=context_pulse(cursor=baseline['next_cursor'],recent_limit=1)
assert [x['event_id'] for x in page1['recent_evidence']]==['a']
assert page1['recent_has_more'] is True
frozen=page1['snapshot_max_event_watermark']

# This arrival happens while the frozen snapshot is being paged.
assert insert_events([event('after-snapshot',4)])==1
page2=context_pulse(cursor=page1['next_cursor'],recent_limit=1)
assert page2['snapshot_max_event_watermark']==frozen
assert [x['event_id'] for x in page2['recent_evidence']]==['b']
page3=context_pulse(cursor=page2['next_cursor'],recent_limit=1)
assert [x['event_id'] for x in page3['recent_evidence']]==['c']
assert page3['recent_has_more'] is False

next_pulse=context_pulse(cursor=page3['next_cursor'],recent_limit=10)
assert [x['event_id'] for x in next_pulse['recent_evidence']]==['after-snapshot']
''', tmp_path)


def test_context_pulse_architecture_stays_observe_independent_and_factual():
    service=(ROOT/'server'/'context_pulse.py').read_text(encoding='utf-8')
    assert 'hidden_frameworks' not in service
    assert 'observation_active' not in service
    assert 'candidate_tasks' not in service
    assert 'factual_context_timeline' in service
    assert 'task_inference_used' in service
    assert 'recommendations_or_advice' in service


def test_context_pulse_route_is_registered_in_production_runner():
    runner=(ROOT/'server'/'enterprise_runner.py').read_text(encoding='utf-8')
    assert 'import server.context_pulse_routes' in runner
