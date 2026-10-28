"""R10 local signal inbox: state-pinned manual review, no action/recipient grants."""
from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signal_inbox as inbox
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from test_event_log_signals import _log, _rule, _csv, CASES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def local_log_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_inbox_review_is_explicit_state_pinned_and_silent():
    log = _log()
    rule = signals.add_rule(log, _rule("Too many", kind="kpi",
        metric="case_count", operator="above", threshold=1))
    rule_id = rule["rule_id"]
    not_run = inbox.list_inbox(log)
    assert not_run["items"][0]["status"] == "not_evaluated"
    assert not_run["unreviewed"] == 0
    assert not not_run["delivery_enabled"] and not not_run["actions_authorized"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.review(log, rule_id, {"state_fingerprint": "0" * 64}, acknowledge=True)

    signals.evaluate(log)
    item = inbox.list_inbox(log)["items"][0]
    assert item["status"] == "unreviewed" and item["requires_review"]
    fingerprint = item["state_fingerprint"]
    assert len(fingerprint) == 64

    reviewed = inbox.review(log, rule_id, {"state_fingerprint": fingerprint}, acknowledge=True)
    assert reviewed["status"] == "acknowledged" and reviewed["acknowledged_at"]
    repeat = inbox.review(log, rule_id, {"state_fingerprint": fingerprint}, acknowledge=True)
    assert repeat == reviewed, "A retry must not rewrite the human's acknowledgement time"
    again = signals.evaluate(log)
    assert again["triggered"] == 1
    assert inbox.list_inbox(log)["items"][0]["status"] == "acknowledged"

    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.review(log, rule_id, {"state_fingerprint": "0" * 64}, acknowledge=False)
    reopened = inbox.review(log, rule_id, {"state_fingerprint": fingerprint}, acknowledge=False)
    assert reopened["status"] == "unreviewed" and not reopened["acknowledged_at"]
    assert inbox.list_inbox(log)["unreviewed"] == 1


def test_new_result_invalidates_old_review_and_rule_deletion_erases_it():
    log = _log()
    rule = signals.add_rule(log, _rule("Nonempty", kind="kpi",
        metric="case_count", operator="above", threshold=0))
    rid = rule["rule_id"]
    signals.evaluate(log)
    prior = inbox.list_inbox(log)["items"][0]["state_fingerprint"]
    inbox.review(log, rid, {"state_fingerprint": prior}, acknowledge=True)
    # Existing snapshots are immutable; a new observed case changes the signal
    # without changing the saved rule or losing its review history.
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("""INSERT INTO events SELECT log_id, 'signal_new_event', 'Receive',
                ts, resource, attributes FROM events WHERE log_id=? LIMIT 1""", [log])
            conn.execute("""INSERT INTO objects SELECT log_id, 'newcase', object_type, attributes
                FROM objects WHERE log_id=? LIMIT 1""", [log])
            conn.execute("""INSERT INTO event_objects
                SELECT log_id, 'signal_new_event', 'newcase', qualifier
                FROM event_objects WHERE log_id=? LIMIT 1""", [log])
        finally:
            conn.close()
    signals.evaluate(log)
    current = inbox.list_inbox(log)["items"][0]
    assert current["status"] == "unreviewed" and current["state_fingerprint"] != prior
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.review(log, rid, {"state_fingerprint": prior}, acknowledge=True)
    inbox.review(log, rid, {"state_fingerprint": current["state_fingerprint"]}, acknowledge=True)
    signals.delete_rule(log, rid)
    assert inbox.list_inbox(log)["items"] == []
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM signal_reviews").fetchone()[0] == 0
    finally:
        conn.close()


def test_append_of_identical_data_invalidates_old_evaluation_and_acknowledgement():
    log = _log()
    rule = signals.add_rule(log, _rule("Nonempty", kind="kpi",
        metric="case_count", operator="above", threshold=0))
    rid = rule["rule_id"]
    signals.evaluate(log)
    before = inbox.list_inbox(log)["items"][0]
    assert before["status"] == "unreviewed"
    inbox.review(log, rid, {"state_fingerprint": before["state_fingerprint"]}, acknowledge=True)
    # Even a fully duplicate data export is a new import generation. The
    # measured KPI may remain unchanged, but its freshness proof has changed.
    second = workspace.append_log(log, BytesIO(_csv(CASES)), source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time", "resource_column": "who"})
    assert second["added_events"] == 0
    stale = inbox.list_inbox(log)["items"][0]
    assert stale["status"] == "stale" and stale["state_fingerprint"] is None
    assert stale["acknowledged_at"] is None
    with pytest.raises(workspace.EventLogWorkspaceError, match="stale"):
        inbox.review(log, rid, {"state_fingerprint": before["state_fingerprint"]}, acknowledge=True)
    signals.evaluate(log)
    fresh = inbox.list_inbox(log)["items"][0]
    assert fresh["status"] == "unreviewed"
    assert fresh["state_fingerprint"] != before["state_fingerprint"]
    assert not fresh["acknowledged_at"]



def test_clear_then_retrigger_requires_another_review():
    log = _log()
    rule = signals.add_rule(log, _rule("threshold", kind="kpi",
        metric="case_count", operator="above", threshold=1))
    rid = rule["rule_id"]
    signals.evaluate(log)
    initial = inbox.list_inbox(log)["items"][0]["state_fingerprint"]
    inbox.review(log, rid, {"state_fingerprint": initial}, acknowledge=True)
    # Force a controlled rule-evaluation state transition to inspect review
    # cleanup independent of the business condition or upstream import.
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("UPDATE signal_rules SET definition=? WHERE log_id=? AND rule_id=?",
                [json.dumps(_rule("threshold", kind="kpi", metric="case_count",
                                  operator="above", threshold=100)), log, rid])
        finally:
            conn.close()
    signals.evaluate(log)
    assert inbox.list_inbox(log)["items"][0]["status"] == "cleared"
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM signal_reviews").fetchone()[0] == 0
            conn.execute("UPDATE signal_rules SET definition=? WHERE log_id=? AND rule_id=?",
                [json.dumps(_rule("threshold", kind="kpi", metric="case_count",
                                  operator="above", threshold=1)), log, rid])
        finally:
            conn.close()
    signals.evaluate(log)
    assert inbox.list_inbox(log)["items"][0]["status"] == "unreviewed"


def test_reviews_are_log_scoped_erasable_and_reject_bad_payloads():
    one, two = _log(), _log()
    rule = signals.add_rule(one, _rule("Nonempty", kind="kpi",
        metric="case_count", operator="above", threshold=0))
    rid = rule["rule_id"]
    signals.evaluate(one)
    fingerprint = inbox.list_inbox(one)["items"][0]["state_fingerprint"]
    for spec in [{}, {"state_fingerprint": fingerprint, "approve_actions": True},
                 {"state_fingerprint": 42}, {"state_fingerprint": ""}]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            inbox.review(one, rid, spec, acknowledge=True)
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.review(two, rid, {"state_fingerprint": fingerprint}, acknowledge=True)
    inbox.review(one, rid, {"state_fingerprint": fingerprint}, acknowledge=True)
    workspace.delete_log(one)
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.list_inbox(one)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM signal_reviews").fetchone()[0] == 0
    finally:
        conn.close()


def test_http_signal_inbox_requires_dashboard_and_rejects_stale_review(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n"
with TestClient(app) as client:
    auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=signal-review&case_column=case"
                       "&activity_column=activity&timestamp_column=time",
                       content=csv, headers={**auth, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    log = made.json()["log_id"]
    base = "/v1/event-logs/" + log
    rule = client.post(base + "/signals", headers=auth, json={
        "name": "Active", "when": {"kind": "kpi", "metric": "case_count",
                                 "operator": "above", "threshold": 0}})
    assert rule.status_code == 200, rule.text
    client.post(base + "/signals/evaluate", headers=auth).raise_for_status()
    item = client.get(base + "/signal-inbox", headers=auth).json()["items"][0]
    rid = item["rule_id"]
    fingerprint = item["state_fingerprint"]
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(base + "/signal-inbox", headers=bearer).status_code in (401, 403)
    assert client.post(base + "/signal-inbox/" + rid + "/acknowledge",
                       headers=bearer, json={"state_fingerprint": fingerprint}).status_code in (401, 403)
    bad = client.post(base + "/signal-inbox/" + rid + "/acknowledge",
                      headers=auth, json={"state_fingerprint": "0" * 64})
    assert bad.status_code == 422
    good = client.post(base + "/signal-inbox/" + rid + "/acknowledge",
                       headers=auth, json={"state_fingerprint": fingerprint})
    assert good.status_code == 200 and good.json()["status"] == "acknowledged", good.text
    reopened = client.post(base + "/signal-inbox/" + rid + "/reopen",
                           headers=auth, json={"state_fingerprint": fingerprint})
    assert reopened.status_code == 200 and reopened.json()["status"] == "unreviewed"
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
