"""R10 batch signal review: exact-state atomicity, local authorization and UI."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signal_inbox as inbox
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from test_event_log_signals import _log, _rule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def separate_dataset(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _evaluated_pair():
    log_id = _log()
    for name in ("Backlog A", "Backlog B"):
        signals.add_rule(log_id, _rule(
            name, kind="kpi", metric="case_count", operator="above", threshold=0))
    signals.evaluate(log_id)
    items = inbox.list_inbox(log_id)["items"]
    assert len(items) == 2 and all(x["status"] == "unreviewed" for x in items)
    return log_id, [
        {"rule_id": x["rule_id"], "state_fingerprint": x["state_fingerprint"]}
        for x in items
    ]


def test_bulk_ack_is_atomic_and_idempotent():
    log_id, reviews = _evaluated_pair()
    before = inbox.list_inbox(log_id)
    assert before["unreviewed"] == 2
    result = inbox.acknowledge_batch(log_id, {"reviews": reviews})
    assert result["reviewed"] == 2 and result["unreviewed"] == 0
    assert all(x["status"] == "acknowledged" for x in result["items"])
    original_times = [x["acknowledged_at"] for x in result["items"]]
    repeat = inbox.acknowledge_batch(log_id, {"reviews": reviews})
    assert [x["acknowledged_at"] for x in repeat["items"]] == original_times
    assert repeat["items"] == result["items"]


def test_one_stale_fingerprint_rolls_back_whole_batch():
    log_id, reviews = _evaluated_pair()
    invalid = [reviews[0], {**reviews[1], "state_fingerprint": "0" * 64}]
    with pytest.raises(workspace.EventLogWorkspaceError, match="stale"):
        inbox.acknowledge_batch(log_id, {"reviews": invalid})
    assert inbox.list_inbox(log_id)["unreviewed"] == 2
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            assert conn.execute("SELECT count(*) FROM signal_reviews WHERE log_id=?",
                                [log_id]).fetchone()[0] == 0
        finally:
            conn.close()


def test_append_basis_change_invalidates_all_current_results():
    log_id, reviews = _evaluated_pair()
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("UPDATE logs SET events=events+1 WHERE log_id=?", [log_id])
        finally:
            conn.close()
    assert all(x["status"] == "stale" for x in inbox.list_inbox(log_id)["items"])
    with pytest.raises(workspace.EventLogWorkspaceError, match="stale"):
        inbox.acknowledge_batch(log_id, {"reviews": reviews})
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM signal_reviews WHERE log_id=?",
                            [log_id]).fetchone()[0] == 0
    finally:
        conn.close()


def test_snoozed_item_requires_explicit_resume_before_batch():
    log_id, reviews = _evaluated_pair()
    inbox.snooze(log_id, reviews[1]["rule_id"], {
        "state_fingerprint": reviews[1]["state_fingerprint"], "minutes": 60})
    with pytest.raises(workspace.EventLogWorkspaceError, match="snoozed"):
        inbox.acknowledge_batch(log_id, {"reviews": reviews})
    assert inbox.list_inbox(log_id)["unreviewed"] == 1
    inbox.snooze(log_id, reviews[1]["rule_id"], {
        "state_fingerprint": reviews[1]["state_fingerprint"], "minutes": 0})
    assert inbox.acknowledge_batch(log_id, {"reviews": reviews})["unreviewed"] == 0


@pytest.mark.parametrize("payload", [
    {}, [], {"reviews": []}, {"reviews": "bad"},
    {"reviews": [{"rule_id": "bad", "state_fingerprint": "0" * 64}]},
    {"reviews": [{"rule_id": "sig_" + "0" * 16, "state_fingerprint": "bad"}]},
    {"reviews": [{"rule_id": "sig_" + "0" * 16, "state_fingerprint": "0" * 64,
                  "approve_actions": True}]},
    {"reviews": [{}] * 21},
])
def test_malformed_batch_is_rejected(payload):
    log_id, _ = _evaluated_pair()
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.acknowledge_batch(log_id, payload)
    assert inbox.list_inbox(log_id)["unreviewed"] == 2


def test_duplicate_ids_and_wrong_log_are_refused():
    log_id, reviews = _evaluated_pair()
    with pytest.raises(workspace.EventLogWorkspaceError, match="duplicate"):
        inbox.acknowledge_batch(log_id, {"reviews": [reviews[0], reviews[0]]})
    second_log = _log()
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown signal"):
        inbox.acknowledge_batch(second_log, {"reviews": reviews})
    assert inbox.list_inbox(log_id)["unreviewed"] == 2


def test_http_batch_review_requires_dashboard_session_and_ui_control(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n"
with TestClient(app) as client:
    auth = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    imported = client.post("/v1/event-logs/import?format=csv&name=batch-review&case_column=case"
                           "&activity_column=activity&timestamp_column=time",
                           content=csv, headers={**auth, "x-owg-local-storage-confirmed": "true"})
    assert imported.status_code == 200, imported.text
    base = "/v1/event-logs/" + imported.json()["log_id"]
    for name in ("A", "B"):
        response = client.post(base + "/signals", headers=auth, json={
            "name": name, "when": {"kind": "kpi", "metric": "case_count",
                                   "operator": "above", "threshold": 0}})
        assert response.status_code == 200, response.text
    evaluated = client.post(base + "/signals/evaluate", headers=auth)
    assert evaluated.status_code == 200, evaluated.text
    items = client.get(base + "/signal-inbox", headers=auth).json()["items"]
    reviews = [{"rule_id": item["rule_id"], "state_fingerprint": item["state_fingerprint"]}
               for item in items]
    route = base + "/signal-inbox/acknowledge-batch"
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(route, headers=bearer, json={"reviews": reviews}).status_code in (401, 403)
    assert client.post(route, headers=auth, json={"reviews": [
        reviews[0], {**reviews[1], "state_fingerprint": "0" * 64}
    ]}).status_code == 422
    assert client.get(base + "/signal-inbox", headers=auth).json()["unreviewed"] == 2
    accepted = client.post(route, headers=auth, json={"reviews": reviews})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["unreviewed"] == 0
    assert accepted.json()["actions_authorized"] is False
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\\nstderr={result.stderr}"
    dashboard = (ROOT / "dashboard" / "event_log_workspace.js").read_text(encoding="utf-8")
    assert "owgSignalBulkAcknowledge" in dashboard
    assert "acknowledge-batch" in dashboard
    assert "selected.size>20" in dashboard
