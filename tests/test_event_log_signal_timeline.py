"""R10: historical signal transitions across local datasets, not live notifications."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from server import event_log_signal_timeline as timeline
from server import event_log_signal_transitions as transitions
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from test_event_log_signals import _log, _rule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _add(log_id, name):
    return signals.add_rule(log_id, _rule(
        name, kind="kpi", metric="case_count", operator="above", threshold=0
    ))["rule_id"]


def test_empty_timeline_and_rejected_parameters():
    out = timeline.timeline()
    assert out["format"] == timeline.FORMAT
    assert out["counts"] == {"triggered": 0, "cleared": 0}
    assert out["retained_total"] == 0 and out["items"] == []
    assert out["matched_transitions"] == 0 and not out["truncated"]
    assert out["source_data_read"] is False
    assert out["evaluations_performed"] is False
    assert out["notifications_sent"] is False
    assert out["actions_authorized"] is False
    for invalid in (None, "", "changed", "<script>", [], {}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            timeline.timeline(transition=invalid)
    for invalid in (0, -1, 201, True, "2", 1.2):
        with pytest.raises(workspace.EventLogWorkspaceError):
            timeline.timeline(limit=invalid)


def test_retained_cross_log_history_counts_filter_and_safe_projection():
    a, b = _log(), _log()
    ra, rb = _add(a, "First review"), _add(b, "Second review")
    signals.evaluate(a)
    signals.evaluate(b)
    all_changes = timeline.timeline()
    assert all_changes["counts"] == {"triggered": 2, "cleared": 0}
    assert all_changes["matched_transitions"] == 2
    assert {x["log_id"] for x in all_changes["items"]} == {a, b}
    assert {x["rule_id"] for x in all_changes["items"]} == {ra, rb}
    assert {x["name"] for x in all_changes["items"]} == {"First review", "Second review"}
    assert all(x["transition"] == "triggered" and x["evaluated_at"].endswith("Z")
               for x in all_changes["items"])
    assert all_changes["items"] == sorted(
        all_changes["items"], key=lambda x: (x["evaluated_at"], x["event_id"]), reverse=True
    )
    assert not all_changes["current_state_check"]
    assert not all_changes["historical_backfill"]
    assert "state_fingerprint" not in json.dumps(all_changes)
    assert "evidence_cases" not in json.dumps(all_changes)
    assert "source_records" not in json.dumps(all_changes)
    one = timeline.timeline(limit=1)
    assert one["retained_total"] == 2 and one["matched_transitions"] == 2
    assert one["returned_transitions"] == 1 and one["truncated"]
    assert timeline.timeline(transition="cleared")["items"] == []
    assert timeline.timeline(transition="triggered")["returned_transitions"] == 2

    # This test creates an explicit synthetic clearance in the existing local
    # journal. It is not an external notification or a new business action.
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert transitions.append(conn, a, ra, {"triggered": True}, {"triggered": False},
                                      datetime.now(timezone.utc))
        finally:
            conn.close()
    after = timeline.timeline()
    assert after["counts"] == {"triggered": 2, "cleared": 1}
    assert after["matched_transitions"] == 3
    assert timeline.timeline(transition="cleared")["returned_transitions"] == 1
    assert timeline.timeline(transition="cleared")["items"][0]["log_id"] == a
    assert timeline.timeline(transition="triggered")["returned_transitions"] == 2

    # Removing a source rule removes its journal, and it cannot remain visible
    # from a stale cross-log index.
    signals.delete_rule(b, rb)
    assert timeline.timeline()["retained_total"] == 2
    workspace.delete_log(a)
    assert timeline.timeline()["retained_total"] == 0


def test_invalid_stored_name_does_not_crash_or_render_mark_up():
    log = _log()
    rid = _add(log, "Before corruption")
    signals.evaluate(log)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("UPDATE signal_rules SET definition=? WHERE log_id=? AND rule_id=?",
                         ["not valid JSON", log, rid])
        finally:
            conn.close()
    current = timeline.timeline()
    assert current["items"][0]["name"] == "Signal name unavailable"
    assert current["counts"]["triggered"] == 1


def test_dashboard_route_denies_bearer_and_is_read_only(tmp_path):
    script = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

with TestClient(app) as client:
    url = "/v1/event-logs/signal-timeline"
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(url).status_code in (401, 403)
    assert client.get(url, headers=bearer).status_code in (401, 403)
    empty = client.get(url, headers=owner)
    assert empty.status_code == 200, empty.text
    assert empty.headers.get("Cache-Control") == "no-store"
    assert empty.json()["retained_total"] == 0
    assert empty.json()['next_before'] is None
    csv = b"case,activity,time\n1,Open,2024-01-01T00:00:00Z\n1,Close,2024-01-02T00:00:00Z\n"
    imported = client.post("/v1/event-logs/import?format=csv&name=History"
                           "&case_column=case&activity_column=activity&timestamp_column=time",
                           content=csv, headers={**owner, "x-owg-local-storage-confirmed": "true"})
    assert imported.status_code == 200, imported.text
    log = imported.json()["log_id"]
    rule = client.post("/v1/event-logs/"+log+"/signals", json={
        "name":"Open cases", "when":{"kind":"kpi","metric":"case_count",
        "operator":"above","threshold":0}}, headers=owner)
    assert rule.status_code == 200, rule.text
    checked = client.post("/v1/event-logs/"+log+"/signals/evaluate", headers=owner)
    assert checked.status_code == 200, checked.text
    result = client.get(url+"?transition=triggered", headers=owner)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["matched_transitions"] == 1
    assert payload["items"][0]["log_id"] == log
    assert payload["items"][0]["transition"] == "triggered"
    assert "state_fingerprint" not in str(payload)
    assert client.get(url+"?transition=nope", headers=owner).status_code == 422
    assert client.get(url+"?limit=201", headers=owner).status_code == 422
    assert client.get(url+"?before=bad!", headers=owner).status_code == 422
    assert client.post(url, json={}, headers=owner).status_code == 405
    episode_url="/v1/event-logs/signal-episode-review?start_date=2026-01-01&end_date=2026-12-31"
    assert client.get(episode_url,headers=bearer).status_code in (401,403)
    ep=client.get(episode_url,headers=owner)
    assert ep.status_code==200,ep.text
    assert ep.headers["cache-control"]=="no-store"
    assert not ep.json()["evaluations_performed"]
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    run = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, f"stdout={run.stdout}\nstderr={run.stderr}"


def test_dashboard_has_filter_drilldown_and_no_approval_controls():
    js = (ROOT / "dashboard/event_log_workspace.js").read_text(encoding="utf-8")
    routes = (ROOT / "server/event_log_routes.py").read_text(encoding="utf-8")
    assert "owgSignalTimelineFilter" in js
    assert "owgSignalTimelineResults" in js
    assert "await refreshTimeline();" in js
    assert "'/v1/event-logs/signal-timeline?'" in js
    assert "await refreshLogs(item.log_id)" in js
    assert '@router.get("/v1/event-logs/signal-timeline")' in routes
    assert "_require_dashboard(request)" in routes.split(
        "def event_log_signal_timeline(")[1].split("@router.post", 1)[0]


def test_timeline_pages_stable_order_with_ties_and_newer_insertions():
    log = _log()
    rule = _add(log, 'History pages')
    at = datetime(2024, 3, 1, tzinfo=timezone.utc).replace(tzinfo=None)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.executemany('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)', [
                [log, rule, f'sevt_{i:024x}', at, 'triggered' if i % 2 else 'cleared', 'a'*64]
                for i in range(205)])
        finally:
            conn.close()
    first = timeline.timeline(limit=50)
    assert len(first['items']) == 50 and first['next_before']
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)',
                         [log, rule, f'sevt_{9999:024x}', at, 'triggered', 'b'*64])
        finally:
            conn.close()
    pages = [first]
    while pages[-1]['next_before'] is not None:
        pages.append(timeline.timeline(limit=50, before=pages[-1]['next_before']))
    assert [len(page['items']) for page in pages] == [50, 50, 50, 50, 5]
    ids = [item['event_id'] for page in pages for item in page['items']]
    assert len(ids) == len(set(ids)) == 205
    assert ids == sorted(ids, reverse=True)
    assert timeline.timeline(limit=1)['items'][0]['event_id'] == f'sevt_{9999:024x}'
    assert pages[-1]['counts'] == {'triggered': 103, 'cleared': 103}
    assert not any('state_fingerprint' in item for page in pages for item in page['items'])


def test_timeline_rejects_invalid_cursors():
    import base64
    def encoded(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    for bad in (None, False, [], '!', 'a'*257,
                encoded({'at':'2024-99-01T00:00:00Z','id':'sevt_'+'0'*24}),
                encoded({'at':'2024-01-01T00:00:00Z','id':'bad'}),
                encoded({'at':'2024-01-01T00:00:00Z','id':'sevt_'+'0'*24,'extra':1})):
        with pytest.raises(workspace.EventLogWorkspaceError, match='cursor'):
            timeline.timeline(before=bad)


def test_episode_review_retained_rows_and_uncertain_boundaries():
    from server import event_log_signal_episode_review as episodes
    from datetime import timedelta
    log=_log();rid=_add(log,"Episode test")
    base=datetime(2026,1,1)
    events=[(0,"cleared"),(1,"triggered"),(3,"cleared"),(4,"triggered"),
            (5,"triggered"),(6,"triggered"),(6,"cleared"),(7,"cleared"),
            (8,"triggered")]
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            conn.executemany("INSERT INTO signal_transitions VALUES (?,?,?,?,?,?)",
                [[log,rid,f"sevt_{i:024x}",base+timedelta(hours=h),kind,"a"*64]
                 for i,(h,kind) in enumerate(events)])
        finally:conn.close()
    args={"start_date":"2026-01-01","end_date":"2026-01-02"}
    r=episodes.review(**args,limit=2)
    assert r["retained_transition_count"]==r["accounted_transitions"]==9
    assert r["status_counts"]=={"cleared":1,"unresolved_at_window_end":1,
        "interrupted_by_repeated_trigger":1,"interrupted_by_ambiguous_timestamp":1,
        "clearance_without_retained_trigger":2,"ambiguous_timestamp":1}
    assert r["observed_trigger_to_clear_seconds"]["p50"]==7200
    assert r["retained_trigger_count"]==5
    p=episodes.review(**args,status="ambiguous_timestamp",expected_review_sha256=r["review_sha256"])
    assert p["page"]["matching_rows"]==1
    assert p["page"]["rows"][0]["ambiguous_trigger_count"]==1
    assert p["page"]["rows"][0]["ambiguous_clear_count"]==1
    second=episodes.review(**args,offset=2,limit=2,expected_review_sha256=r["review_sha256"])
    assert second["review_sha256"]==r["review_sha256"]
    assert "state_fingerprint" not in json.dumps(second)
    signals.delete_rule(log,rid)
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        episodes.review(**args,expected_review_sha256=r["review_sha256"])


def test_episode_review_window_edges_and_validation():
    from server import event_log_signal_episode_review as episodes
    log=_log();rid=_add(log,"Window")
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            conn.executemany("INSERT INTO signal_transitions VALUES (?,?,?,?,?,?)",[
                [log,rid,"sevt_"+"1"*24,datetime(2025,12,31,23),"triggered","a"*64],
                [log,rid,"sevt_"+"2"*24,datetime(2026,1,1),"cleared","a"*64],
                [log,rid,"sevt_"+"3"*24,datetime(2026,1,1,23),"triggered","a"*64],
                [log,rid,"sevt_"+"4"*24,datetime(2026,1,2),"cleared","a"*64]])
        finally:conn.close()
    args={"start_date":"2026-01-01","end_date":"2026-01-02"}
    r=episodes.review(**args)
    assert r["retained_transition_count"]==2
    assert r["status_counts"]["clearance_without_retained_trigger"]==1
    assert r["status_counts"]["unresolved_at_window_end"]==1
    assert r["observed_trigger_to_clear_seconds"]["p50"] is None
    for change in ({"start_date":"2026-02-01"},{"end_date":"2028-01-01"},{"start_date":"2026-1-1"},
                   {"limit":True},{"status":"bad"},{"offset":1},{"expected_review_sha256":"bad"}):
        with pytest.raises(workspace.EventLogWorkspaceError):episodes.review(**{**args,**change})


def test_episode_budget_and_empty_window(monkeypatch):
    from server import event_log_signal_episode_review as episodes
    log=_log();rid=_add(log,"Budget")
    with workspace._LOCK:
        conn=workspace._connect()
        try:conn.execute("INSERT INTO signal_transitions VALUES (?,?,?,?,?,?)",
            [log,rid,"sevt_"+"1"*24,datetime(2026,1,1),"triggered","a"*64])
        finally:conn.close()
    args={"start_date":"2026-01-01","end_date":"2026-01-02"}
    monkeypatch.setattr(episodes,"MAX_TRANSITIONS",0)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):episodes.review(**args)
    r=episodes.review(start_date="2026-02-01",end_date="2026-02-02")
    assert r["retained_transition_count"]==0
    assert r["observed_trigger_to_clear_seconds"]["count"]==0
    assert not r["actions_authorized"]
