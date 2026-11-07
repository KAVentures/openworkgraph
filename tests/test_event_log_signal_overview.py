"""R10: cross-log signal operations overview reuses authoritative local review states."""
from __future__ import annotations

import json
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signal_overview as overview
from server import event_log_signal_inbox as inbox
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from test_event_log_signals import _log, _rule, _csv, CASES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setattr(scheduler, "DATA_DIR", tmp_path)
    yield


def _add(log_id, name, threshold):
    return signals.add_rule(log_id, _rule(name, kind="kpi", metric="case_count",
                                         operator="above", threshold=threshold))["rule_id"]


def test_empty_overview_is_exact_and_does_not_create_rules():
    before = overview.overview()
    assert before["datasets_scanned"] == 0
    assert before["total_rules"] == 0 and before["attention"] == 0
    assert before["items"] == [] and not before["truncated"]
    assert not before["source_data_read"] and not before["notifications_sent"]
    assert not before["evaluations_performed"] and not before["actions_authorized"]


def test_across_two_logs_reports_triggered_clear_and_unchecked_without_evaluating():
    a, b = _log(), _log()
    _add(a, "Needs review", 0)
    _add(a, "Never reached", 100)
    _add(b, "Not yet checked", 0)
    signals.evaluate(a)
    result = overview.overview()
    assert result["datasets_scanned"] == 2
    assert result["datasets_with_rules"] == 2
    assert result["total_rules"] == 3
    assert result["counts"]["unreviewed"] == 1
    assert result["counts"]["cleared"] == 1
    assert result["counts"]["not_evaluated"] == 1
    assert result["attention"] == 2
    assert result["matched_rules"] == 2
    assert result["returned_rules"] == 2
    assert {r["name"] for r in result["items"]} == {"Needs review", "Not yet checked"}
    assert all("evidence_cases" not in r and "state_fingerprint" not in r for r in result["items"])
    all_states = overview.overview(status="all")
    assert all_states["matched_rules"] == 3
    assert [r["status"] for r in all_states["items"]] == [
        "unreviewed", "not_evaluated", "cleared"
    ]
    small = overview.overview(status="all", limit=1)
    assert small["truncated"] and small["matched_rules"] == 3
    assert small["returned_rules"] == 1 and small["counts"] == all_states["counts"]


def test_ack_snooze_reopen_and_append_staleness_share_existing_inbox_semantics():
    a, b = _log(), _log()
    x, y = _add(a, "Reviewed", 0), _add(b, "Deferred", 0)
    signals.evaluate(a); signals.evaluate(b)
    fa = inbox.list_inbox(a)["items"][0]["state_fingerprint"]
    fb = inbox.list_inbox(b)["items"][0]["state_fingerprint"]
    inbox.review(a, x, {"state_fingerprint": fa}, acknowledge=True)
    inbox.snooze(b, y, {"state_fingerprint": fb, "minutes": 30})
    result = overview.overview(status="all")
    assert result["counts"]["acknowledged"] == 1 and result["counts"]["snoozed"] == 1
    assert result["attention"] == 0
    assert overview.overview(status="attention")["items"] == []

    inbox.snooze(b, y, {"state_fingerprint": fb, "minutes": 0})
    assert overview.overview()["counts"]["unreviewed"] == 1
    # A new batch can leave all measured values unchanged and STILL render the
    # old result stale; no automatic action or fresh approval is inferred.
    second = workspace.append_log(a, BytesIO(_csv(CASES)), source_format="csv",
                                  mapping={"case_column": "case", "activity_column": "activity",
                                           "timestamp_column": "time", "resource_column": "who"})
    assert second["added_events"] == 0
    updated = overview.overview()
    assert updated["counts"]["stale"] == 1
    assert updated["attention"] == 2
    assert {x["status"] for x in updated["items"]} == {"stale", "unreviewed"}
    assert overview.overview(status="acknowledged")["items"] == []
    signals.evaluate(a)
    assert overview.overview()["counts"]["unreviewed"] == 2
    inbox.review(a, x, {"state_fingerprint": inbox.list_inbox(a)["items"][0]["state_fingerprint"]},
                 acknowledge=True)
    assert overview.overview()["counts"]["acknowledged"] == 1
    workspace.delete_log(a)
    after = overview.overview(status="all")
    assert after["datasets_scanned"] == 1 and after["total_rules"] == 1
    assert all(row["log_id"] == b for row in after["items"])


def test_scheduled_status_is_only_metadata_no_evaluation_or_source_calls(monkeypatch):
    a = _log()
    _add(a, "Scheduled check", 0)
    monkeypatch.setattr(scheduler, "_check_target", lambda kind, target: None)
    item = scheduler.create("event_log_signals", a, interval_minutes=15,
                            confirm_scheduled_runs=True)
    result = overview.overview(status="all")
    assert result["items"][0]["scheduled_check"] is True
    assert result["items"][0]["scheduled_next_at"]
    assert inbox.list_inbox(a)["items"][0]["status"] == "not_evaluated"
    scheduler.update(item["schedule_id"], enabled=False)
    result = overview.overview(status="all")
    assert result["items"][0]["scheduled_check"] is False
    assert result["items"][0]["scheduled_next_at"] is None


def test_invalid_filters_and_bad_persisted_state_fail_closed():
    a = _log()
    rid = _add(a, "Bad evaluation", 0)
    signals.evaluate(a)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            conn.execute("UPDATE signal_rules SET last_state=? WHERE log_id=? AND rule_id=?",
                         ["not JSON", a, rid])
        finally:
            conn.close()
    result = overview.overview()
    assert result["counts"]["unavailable"] == 1
    assert result["attention"] == 1
    assert result["items"][0]["status"] == "unavailable"
    for filter_value in ("", "unexpected", "<script>", None):
        with pytest.raises(workspace.EventLogWorkspaceError):
            overview.overview(status=filter_value)
    for limit in (0, -1, 201, True, "10", 1.5):
        with pytest.raises(workspace.EventLogWorkspaceError):
            overview.overview(limit=limit)


def test_real_session_guard_and_bearer_denial_for_across_log_api(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth

csv = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n"
with TestClient(app) as client:
    url = "/v1/event-logs/signal-overview"
    token = "OWG-Session " + local_auth.create_dashboard_session()
    owner = {"Authorization": token}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.get(url).status_code in (401,403)
    assert client.get(url, headers=bearer).status_code in (401,403)
    empty = client.get(url, headers=owner)
    assert empty.status_code == 200, empty.text
    assert empty.headers.get("Cache-Control") == "no-store"
    assert empty.json()["total_rules"] == 0
    log = client.post("/v1/event-logs/import?format=csv&name=overview"
                       "&case_column=case&activity_column=activity&timestamp_column=time",
                       content=csv, headers={**owner, "x-owg-local-storage-confirmed": "true"})
    assert log.status_code == 200, log.text
    id = log.json()["log_id"]
    rule = client.post("/v1/event-logs/"+id+"/signals", json={
        "name":"Read-only", "when":{"kind":"kpi","metric":"case_count",
            "operator":"above","threshold":0}}, headers=owner)
    assert rule.status_code == 200, rule.text
    r = client.get(url, headers=owner)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["attention"] == 1 and data["counts"]["not_evaluated"] == 1
    assert data["items"][0]["log_id"] == id
    assert "evidence_cases" not in str(data) and "state_fingerprint" not in str(data)
    assert client.get(url+"?status=nope", headers=owner).status_code == 422
    assert client.get(url+"?limit=201", headers=owner).status_code == 422
    assert client.post(url, json={}, headers=owner).status_code == 405
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


def test_ui_has_real_deep_link_and_discloses_scanned_scope():
    ui = (ROOT / "dashboard/event_log_workspace.js").read_text(encoding="utf-8")
    routes = (ROOT / "server/event_log_routes.py").read_text(encoding="utf-8")
    assert 'id=\\'owgSignalAllResults\\'' in ui
    assert "refreshLogs(item.log_id)" in ui
    assert "await analyse()" in ui
    assert "Showing '+\\n" in ui or "Showing '" in ui
    assert "'/v1/event-logs/signal-overview?'" in ui
    assert '"\\/v1\\/event-logs\\/signal-overview"' not in ui  # no hardcoded fake response
    assert '@router.get("/v1/event-logs/signal-overview")' in routes
    assert "_require_dashboard(request)" in routes.split("def event_log_signal_overview(")[1].split(
        "@router.post", 1)[0]
