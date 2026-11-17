"""R10: state-pinned, counts-only transitions in the local signal inbox."""
from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signal_transitions as transitions
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from test_event_log_signals import _csv, _log, _rule

ROOT = Path(__file__).resolve().parents[1]
MAPPING = {
    "case_column": "case", "activity_column": "activity",
    "timestamp_column": "time", "resource_column": "who"
}


@pytest.fixture(autouse=True)
def local_database(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_transitions_record_trigger_clear_retrigger_once_and_erase_with_rule():
    log = _log({"slow": [("Receive", 0), ("Close", 10)]})
    rid = signals.add_rule(log, _rule(
        "Slow case alert", kind="kpi", metric="median_throughput_seconds",
        operator="above", threshold=2 * 3600))["rule_id"]
    first = signals.evaluate(log)
    assert first["triggered"] == 1
    first_history = transitions.list_history(log)
    assert first_history["retained_total"] == 1
    assert first_history["items"][0]["transition"] == "triggered"
    assert len(first_history["items"][0]["state_fingerprint"]) == 64

    # Evaluating an unchanged state cannot produce duplicate alerts.
    signals.evaluate(log)
    assert transitions.list_history(log)["retained_total"] == 1
    fast = {f"fast{i}": [("Receive", 15 + i), ("Close", 15.1 + i)] for i in range(3)}
    workspace.append_log(log, BytesIO(_csv(fast)), source_format="csv", mapping=MAPPING)
    assert signals.evaluate(log)["triggered"] == 0
    history = transitions.list_history(log)
    assert [item["transition"] for item in history["items"]] == ["cleared", "triggered"]
    assert history["retained_total"] == 2

    # Changing only the dataset revision without changing the boolean is NOT
    # another clearance and must not notify/deliver.
    workspace.append_log(log, BytesIO(_csv({
        "fast4": [("Receive", 20), ("Close", 20.1)]
    })), source_format="csv", mapping=MAPPING)
    signals.evaluate(log)
    assert transitions.list_history(log)["retained_total"] == 2

    slow = {f"slow{i}": [("Receive", 30 + i), ("Close", 40 + i)] for i in range(5)}
    workspace.append_log(log, BytesIO(_csv(slow)), source_format="csv", mapping=MAPPING)
    assert signals.evaluate(log)["triggered"] == 1
    again = transitions.list_history(log)
    assert [i["transition"] for i in again["items"]] == ["triggered", "cleared", "triggered"]
    assert again["truncated"] is False
    assert again["delivery_enabled"] is False and again["actions_authorized"] is False
    assert again["historical_backfill"] is False
    assert all(item["name"] == "Slow case alert" for item in again["items"])
    assert "slow0" not in str(again) and "fast0" not in str(again)

    assert transitions.list_history(log, limit=1)["truncated"] is True
    with pytest.raises(workspace.EventLogWorkspaceError):
        transitions.list_history(log, limit=0)
    with pytest.raises(workspace.EventLogWorkspaceError):
        transitions.list_history(log, limit=201)

    signals.delete_rule(log, rid)
    assert transitions.list_history(log)["retained_total"] == 0


def test_initial_cleared_does_not_create_a_fake_event_and_log_delete_cleans_history():
    log = _log({"a": [("Start", 0), ("Close", 1)]})
    signals.add_rule(log, _rule("Never", kind="kpi", metric="case_count",
                                operator="above", threshold=10))
    signals.evaluate(log)
    assert transitions.list_history(log)["items"] == []
    assert transitions.list_history(log)["retained_total"] == 0

    signals.add_rule(log, _rule("Active", kind="kpi", metric="case_count",
                                operator="above", threshold=0))
    signals.evaluate(log)
    assert transitions.list_history(log)["retained_total"] == 1
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        transitions.list_history(log)


def test_failed_evaluation_cannot_leave_partial_states_or_transition_events(monkeypatch):
    log = _log({"a": [("Start", 0), ("Close", 1)]})
    ids = [signals.add_rule(log, _rule(
        "Active " + str(i), kind="kpi", metric="case_count",
        operator="above", threshold=0))["rule_id"] for i in range(2)]
    original = transitions.append
    seen = []

    def fail_after_one(conn, log_id, rule_id, previous, state, at):
        seen.append(rule_id)
        if len(seen) == 2:
            raise RuntimeError("simulated interrupted evaluation")
        return original(conn, log_id, rule_id, previous, state, at)

    monkeypatch.setattr(transitions, "append", fail_after_one)
    with pytest.raises(RuntimeError, match="interrupted"):
        signals.evaluate(log)
    assert len(seen) == 2
    assert transitions.list_history(log)["retained_total"] == 0
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            states = conn.execute(
                "SELECT rule_id,last_state FROM signal_rules WHERE log_id=? ORDER BY rule_id",
                [log]).fetchall()
            assert {rid for rid, _ in states} == set(ids)
            assert all(state is None for _, state in states)
        finally:
            conn.close()


def test_bounded_history_avoids_unbounded_per_log_retention(monkeypatch):
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(transitions, "MAX_RETAINED_PER_LOG", 3)
    log = _log()
    rid = signals.add_rule(log, _rule(
        "State", kind="kpi", metric="case_count", operator="above", threshold=0))["rule_id"]
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            for i in range(5):
                transitions.append(conn, log, rid,
                    {"triggered": i % 2 != 0},
                    {"triggered": i % 2 == 0, "log_basis": [0]},
                    now + timedelta(seconds=i))
            transitions.trim(conn, log)
        finally:
            conn.close()
    result = transitions.list_history(log)
    assert result["retained_total"] == 3
    assert result["retention_limit"] == 3
    assert [i["transition"] for i in result["items"]] == ["triggered", "cleared", "triggered"]


def test_signal_history_route_requires_dashboard_session(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n"
with TestClient(app) as client:
    auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=signal-history&case_column=case"
                       "&activity_column=activity&timestamp_column=time",
                       content=csv, headers={**auth, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    log = made.json()["log_id"]
    base = "/v1/event-logs/" + log
    made = client.post(base + "/signals", headers=auth, json={
        "name": "Active", "when": {"kind": "kpi", "metric": "case_count",
                                  "operator": "above", "threshold": 0}})
    assert made.status_code == 200, made.text
    client.post(base + "/signals/evaluate", headers=auth).raise_for_status()
    path = base + "/signal-history"
    good = client.get(path, headers=auth)
    assert good.status_code == 200, good.text
    body = good.json()
    assert body["retained_total"] == 1
    assert body["items"][0]["transition"] == "triggered"
    assert body["actions_authorized"] is False
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(path, headers=bearer).status_code in (401, 403)
    assert client.get(path, headers=auth, params={"limit": 201}).status_code == 422
    assert client.get(path, headers=auth, params={"before": "bad!"}).status_code == 422
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_per_log_history_pages_tied_events_and_isolates_other_logs():
    from datetime import datetime
    log, other = _log(), _log()
    rule = signals.add_rule(log, _rule('Paged', kind='kpi', metric='case_count', operator='above', threshold=0))['rule_id']
    other_rule = signals.add_rule(other, _rule('Other', kind='kpi', metric='case_count', operator='above', threshold=0))['rule_id']
    at = datetime(2024, 3, 1)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.executemany('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)', [
                [log, rule, f'sevt_{i:024x}', at, 'triggered' if i % 2 else 'cleared', 'a'*64]
                for i in range(205)])
            conn.execute('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)',
                         [other, other_rule, f'sevt_{9999:024x}', at, 'triggered', 'b'*64])
        finally:
            conn.close()
    pages = [transitions.list_history(log, limit=50)]
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute('INSERT INTO signal_transitions VALUES (?, ?, ?, ?, ?, ?)',
                         [log, rule, f'sevt_{9998:024x}', at, 'triggered', 'c'*64])
        finally:
            conn.close()
    while pages[-1]['next_before'] is not None:
        pages.append(transitions.list_history(log, limit=50, before=pages[-1]['next_before']))
    assert [len(page['items']) for page in pages] == [50, 50, 50, 50, 5]
    ids = [item['event_id'] for page in pages for item in page['items']]
    assert len(ids) == len(set(ids)) == 205 and ids == sorted(ids, reverse=True)
    assert f'sevt_{9999:024x}' not in ids and f'sevt_{9998:024x}' not in ids
    assert transitions.list_history(log, limit=1)['items'][0]['event_id'] == f'sevt_{9998:024x}'
    with pytest.raises(workspace.EventLogWorkspaceError, match='cursor'):
        transitions.list_history(log, before='bad!')
