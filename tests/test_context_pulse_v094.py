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
assert all(x['factual_aggregate'] and not x['advice'] for x in first['findings'])
assert all(
    (not x['task_inference_used'])
    or (x['finding_kind']=='repeated_workflow' and x.get('needs_review') is True)
    for x in first['findings']
)

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


def test_finding_limit_drains_same_snapshot_without_marking_unseen_findings_seen(tmp_path):
    _run(r'''
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
            'event_id':f'limit-{day}-{index}',
            'observed_at':(base+timedelta(seconds=offset)).isoformat(),
            'device_id':'d','session_id':f'limit-s{day}','app':app,
            'window_title':app,'event_type':'focus_span','duration_seconds':120,
            'metadata':{'activity':{'foreground_seconds':120,'engaged_seconds':100,'active_input_seconds':40}},
        })
assert insert_events(events)==9

page=context_pulse(recent_limit=20,finding_limit=1)
assert page['findings_returned']==1 and page['findings_has_more'] is True, page
frozen=page['snapshot_max_event_watermark']
seen=set()

while True:
    assert page['snapshot_max_event_watermark']==frozen
    assert page['findings_returned']==1, page
    item=page['findings'][0]
    assert item['status']=='baseline'
    assert item['finding_id'] not in seen
    seen.add(item['finding_id'])
    if not page['findings_has_more']:
        break
    page=context_pulse(cursor=page['next_cursor'],recent_limit=20,finding_limit=1)

assert len(seen)>=3, seen
settled=context_pulse(cursor=page['next_cursor'],recent_limit=20,finding_limit=1)
assert settled['findings']==[], settled
''', tmp_path)


def test_findings_do_not_turn_idle_foreground_or_missing_sessions_into_patterns(tmp_path):
    _run(r'''
from server.context_pulse import _surface_findings, _transition_findings

idle=[]
for day in range(3):
    idle.append({
        'session_id':f'idle-{day}',
        'started_at':f'2026-09-{20+day:02d}T10:00:00+00:00',
        'ended_at':f'2026-09-{20+day:02d}T11:00:00+00:00',
        'work_surface':'Claude',
        'container_app':'Claude',
        'foreground_seconds':3600,
        'engaged_seconds':0,
        'evidence_event_ids':[f'idle-{day}'],
    })
assert _surface_findings(idle,30)==[], _surface_findings(idle,30)

# Without a session ID, chronology across rows is insufficient evidence that two
# surfaces were adjacent parts of one workflow. Do not manufacture a transition.
missing=[]
for i in range(8):
    surface='A' if i%2==0 else 'B'
    missing.append({
        'session_id':'',
        'started_at':f'2026-09-25T10:{i:02d}:00+00:00',
        'ended_at':f'2026-09-25T10:{i:02d}:30+00:00',
        'work_surface':surface,
        'container_app':surface,
        'foreground_seconds':30,
        'engaged_seconds':20,
        'evidence_event_ids':[f'missing-{i}'],
    })
assert _transition_findings(missing,30)==[], _transition_findings(missing,30)
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


def test_pulse_repeated_workflows_use_shared_candidates_not_coarse_family_counts():
    source = (ROOT / "server" / "context_pulse_findings.py").read_text(encoding="utf-8")
    assert "cluster_runs(" in source
    assert '"candidate_cluster_id"' in source
    assert '"coarse_family_keys"' in source
    assert 'by_family' not in source


def test_manual_transfer_finding_rolls_up_alternate_sources_by_destination(monkeypatch):
    from server import context_pulse_findings as findings
    from server import work_profile

    monkeypatch.setattr(findings, "_frozen_context_rows", lambda **_kwargs: [{"surface": "x"}])
    monkeypatch.setattr(
        work_profile,
        "_transfer_patterns",
        lambda _rows: [
            {
                "source_surface": "Microsoft Excel",
                "destination_surface": "Internal Pricing Tool",
                "count": 2,
                "cross_surface": True,
                "example_event_ids": ["e1", "e2"],
            },
            {
                "source_surface": "Adobe Acrobat",
                "destination_surface": "Internal Pricing Tool",
                "count": 2,
                "cross_surface": True,
                "example_event_ids": ["e3", "e4"],
            },
        ],
    )

    rows = findings.manual_transfer_findings(
        snapshot_context_max_id=10,
        snapshot_at="2026-10-06T10:00:00+00:00",
        lookback_days=30,
    )
    assert len(rows) == 1
    assert rows[0]["occurrence_count"] == 4
    assert rows[0]["destination_surface"] == "Internal Pricing Tool"
    assert rows[0]["grouping_basis"] == "destination_surface"
    assert rows[0]["task_identity_inferred"] is False
    assert rows[0]["source_breakdown"] == [
        {"source_surface": "Adobe Acrobat", "occurrence_count": 2},
        {"source_surface": "Microsoft Excel", "occurrence_count": 2},
    ]


def test_agent_recovered_failures_are_not_reported_as_failed_runs(monkeypatch):
    from server import agent_execution_traces, context_pulse_findings as findings, procedural_memory

    monkeypatch.setattr(
        agent_execution_traces,
        "agent_execution_traces",
        lambda *_args, **_kwargs: {
            "executions": [
                {
                    "execution_id": "execution:aaaaaaaaaaaaaaaa",
                    "agent": {"name": "Claude Code"},
                    "started_at": "2026-10-06T09:00:00Z",
                    "structural_steps": ["tool:shell:error", "tool:code:success", "tool:shell:success"],
                },
                {
                    "execution_id": "execution:bbbbbbbbbbbbbbbb",
                    "agent": {"name": "Claude Code"},
                    "started_at": "2026-10-06T10:00:00Z",
                    "structural_steps": ["tool:shell:error", "tool:code:success", "tool:shell:success"],
                },
            ]
        },
    )
    monkeypatch.setattr(
        procedural_memory,
        "derive_executions",
        lambda _raw: [
            {
                "execution_id": "execution:aaaaaaaaaaaaaaaa",
                "actor_kind": "agent",
                "outcome_status": "unknown",
                "recovered_failure_observed": True,
            },
            {
                "execution_id": "execution:bbbbbbbbbbbbbbbb",
                "actor_kind": "agent",
                "outcome_status": "unknown",
                "recovered_failure_observed": True,
            },
        ],
    )

    rows = findings.agent_failure_findings([], 30)
    assert len(rows) == 1
    assert rows[0]["finding_kind"] == "agent_recovered_failure"
    assert rows[0]["recovered_run_count"] == 2
    assert rows[0]["failing_run_count"] == 0
    assert rows[0]["run_outcome_inferred_from_final_tool"] is False


def test_manual_transfer_finding_emits_single_source_destination_at_threshold(monkeypatch):
    from server import context_pulse_findings as findings
    from server import work_profile

    monkeypatch.setattr(findings, "_frozen_context_rows", lambda **_kwargs: [{"surface": "x"}])
    monkeypatch.setattr(
        work_profile,
        "_transfer_patterns",
        lambda _rows: [{
            "source_surface": "Citrix",
            "destination_surface": "Microsoft Excel",
            "count": 3,
            "cross_surface": True,
            "example_event_ids": ["e1", "e2", "e3"],
        }],
    )

    rows = findings.manual_transfer_findings(
        snapshot_context_max_id=10,
        snapshot_at="2026-10-06T10:00:00+00:00",
        lookback_days=30,
    )
    assert len(rows) == 1
    assert rows[0]["source_surface"] == "Citrix"
    assert rows[0]["destination_surface"] == "Microsoft Excel"
    assert rows[0]["occurrence_count"] == 3
    assert rows[0]["grouping_basis"] == "destination_surface"
