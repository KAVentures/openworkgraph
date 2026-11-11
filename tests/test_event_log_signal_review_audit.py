"""R10 audit trail: meaningful, state-pinned local signal review changes only."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signal_review_audit as audit
from server import event_log_signal_inbox as inbox
from server import event_log_signals as signals
from server import event_log_workspace as workspace
from test_event_log_signals import _log, _rule

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _local_db(monkeypatch, tmp_path):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _active_rule(log, name="Alert"):
    rule_id = signals.add_rule(log, _rule(
        name, kind="kpi", metric="case_count",
        operator="above", threshold=0))["rule_id"]
    signals.evaluate(log)
    state = next(x for x in inbox.list_inbox(log)["items"] if x["rule_id"] == rule_id)
    return rule_id, state["state_fingerprint"]


def test_audit_records_only_real_review_state_changes_and_no_sensitive_evidence():
    log = _log()
    rid, fingerprint = _active_rule(log)
    assert audit.list_actions(log)["items"] == []
    assert not audit.list_actions(log)["external_delivery"]
    assert not audit.list_actions(log)["action_authority"]

    inbox.review(log, rid, {"state_fingerprint": fingerprint}, acknowledge=True)
    inbox.review(log, rid, {"state_fingerprint": fingerprint}, acknowledge=True)
    assert [x["action"] for x in audit.list_actions(log)["items"]] == ["acknowledged"]
    inbox.review(log, rid, {"state_fingerprint": fingerprint}, acknowledge=False)
    inbox.review(log, rid, {"state_fingerprint": fingerprint}, acknowledge=False)
    assert [x["action"] for x in audit.list_actions(log)["items"]][:2] == ["reopened", "acknowledged"]
    snooze = {"state_fingerprint": fingerprint, "minutes": 60}
    inbox.snooze(log, rid, snooze)
    inbox.snooze(log, rid, snooze)  # a repeat while active is idempotent
    assert audit.list_actions(log)["items"][0]["action"] == "snoozed"
    assert audit.list_actions(log)["items"][0]["snooze_minutes"] == 60
    inbox.snooze(log, rid, {"state_fingerprint": fingerprint, "minutes": 0})
    inbox.snooze(log, rid, {"state_fingerprint": fingerprint, "minutes": 0})
    events = audit.list_actions(log)["items"]
    assert [x["action"] for x in events] == [
        "resumed", "snoozed", "reopened", "acknowledged"]
    assert all(e["identity"] == "local_dashboard_session_unattributed" for e in events)
    assert all(e["state_fingerprint"] == fingerprint for e in events)
    assert "case_ids" not in str(events)
    assert "slow" not in str(events)
    assert audit.list_actions(log, limit=2)["truncated"]
    with pytest.raises(workspace.EventLogWorkspaceError):
        audit.list_actions(log, limit=201)
    with pytest.raises(workspace.EventLogWorkspaceError):
        audit.list_actions(log, limit=False)
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.review(log, rid, {"state_fingerprint": "f" * 64}, acknowledge=True)
    assert audit.list_actions(log)["retained_total"] == 4


def test_batch_review_is_atomic_and_an_exact_retry_has_no_second_audit_receipt():
    log = _log()
    rid1 = signals.add_rule(log, _rule(
        "Count", kind="kpi", metric="case_count",
        operator="above", threshold=0))["rule_id"]
    rid2 = signals.add_rule(log, _rule(
        "SLA", kind="kpi", metric="median_throughput_seconds",
        operator="above", threshold=1))["rule_id"]
    signals.evaluate(log)
    states = {x["rule_id"]:x["state_fingerprint"] for x in inbox.list_inbox(log)["items"]}
    spec = {"reviews":[{"rule_id":rid,"state_fingerprint":states[rid]} for rid in [rid1,rid2]]}
    inbox.acknowledge_batch(log, spec)
    inbox.acknowledge_batch(log, spec)
    events = audit.list_actions(log)["items"]
    assert len(events) == 2
    assert {e["rule_id"] for e in events} == {rid1,rid2}
    assert all(e["action"] == "batch_acknowledged" for e in events)
    bad = {"reviews":[spec["reviews"][0], {"rule_id":rid2,"state_fingerprint":"0"*64}]}
    with pytest.raises(workspace.EventLogWorkspaceError):
        inbox.acknowledge_batch(log, bad)
    assert audit.list_actions(log)["retained_total"] == 2
    assert all(x["status"] == "acknowledged" for x in inbox.list_inbox(log)["items"])


def test_failed_audit_write_rolls_back_signal_review_and_its_receipt(monkeypatch):
    log = _log()
    rid, fingerprint = _active_rule(log)
    original = audit.record
    def fail_after_insert(conn, *args, **kwargs):
        original(conn, *args, **kwargs)
        raise RuntimeError("interrupted audit receipt")
    monkeypatch.setattr(audit, "record", fail_after_insert)
    with pytest.raises(RuntimeError, match="interrupted"):
        inbox.review(log, rid, {"state_fingerprint":fingerprint}, acknowledge=True)
    assert inbox.list_inbox(log)["items"][0]["status"] == "unreviewed"
    assert audit.list_actions(log)["items"] == []


def test_audit_is_bounded_per_dataset_and_deleted_with_rule_and_log(monkeypatch):
    monkeypatch.setattr(audit, "MAX_RETAINED", 3)
    log = _log()
    rid, fingerprint = _active_rule(log)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            for idx in range(5):
                audit.record(conn, log, rid, fingerprint, "acknowledged",
                             now + timedelta(seconds=idx))
        finally:
            conn.close()
    assert audit.list_actions(log)["retained_total"] == 3
    assert audit.list_actions(log)["retention_limit"] == 3
    assert len(audit.list_actions(log)["items"]) == 3
    signals.delete_rule(log, rid)
    assert audit.list_actions(log)["retained_total"] == 0
    new_id, new_fp = _active_rule(log, name="Other")
    inbox.review(log, new_id, {"state_fingerprint":new_fp}, acknowledge=True)
    assert audit.list_actions(log)["retained_total"] == 1
    workspace.delete_log(log)
    with pytest.raises(workspace.EventLogWorkspaceError):
        audit.list_actions(log)


def test_history_http_rejects_non_dashboard_token(tmp_path):
    script = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
csv = b"case,activity,time\n1,Start,2024-01-01T00:00:00Z\n1,End,2024-01-01T00:00:01Z\n"
with TestClient(app) as client:
    auth={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    imported=client.post("/v1/event-logs/import?format=csv&name=audit&case_column=case"
                         "&activity_column=activity&timestamp_column=time",
                         content=csv,headers={**auth,"x-owg-local-storage-confirmed":"true"})
    imported.raise_for_status()
    log=imported.json()["log_id"]
    root="/v1/event-logs/"+log
    rule=client.post(root+"/signals",headers=auth,json={
        "name":"Count","when":{"kind":"kpi","metric":"case_count",
        "operator":"above","threshold":0}})
    rule.raise_for_status()
    client.post(root+"/signals/evaluate",headers=auth).raise_for_status()
    item=client.get(root+"/signal-inbox",headers=auth).json()["items"][0]
    review=client.post(root+"/signal-inbox/"+item["rule_id"]+"/acknowledge",
        headers=auth,json={"state_fingerprint":item["state_fingerprint"]})
    review.raise_for_status()
    ok=client.get(root+"/signal-review-history",headers=auth)
    assert ok.status_code==200,ok.text
    assert ok.json()["retained_total"]==1
    assert ok.json()["items"][0]["action"]=="acknowledged"
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    assert client.get(root+"/signal-review-history",headers=bearer).status_code in (401,403)
    assert client.get(root+"/signal-review-history",headers=auth,
        params={"limit":201}).status_code==422
"""
    env=os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
                "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
                "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")})
    result=subprocess.run([sys.executable,"-c",script],cwd=ROOT,env=env,
        capture_output=True,text=True,timeout=120)
    assert result.returncode==0,f"stdout={result.stdout}\nstderr={result.stderr}"
