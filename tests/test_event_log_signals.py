"""Saved signal rules over event logs (roadmap #326 F2)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_signals as signals
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _csv(cases):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time", "who"])
    for case, steps in cases.items():
        for activity, hours in steps:
            writer.writerow([case, activity, (START + timedelta(hours=hours)).isoformat(), "ann"])
    return out.getvalue().encode()


CASES = {
    "fast": [("Receive", 0), ("Check", 1), ("Close", 2)],
    "slow": [("Receive", 0), ("Check", 30), ("Check", 40), ("Close", 50)],
    "open": [("Receive", 10), ("Check", 12)],
    "late": [("Receive", 90), ("Check", 100)],  # the log's latest event
}


def _log(cases=CASES):
    return workspace.import_log(BytesIO(_csv(cases)), name="signals", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def _rule(name, **when):
    return {"name": name, "when": when}


def test_kpi_and_case_rules_trigger_with_evidence_and_remember_new_cases():
    log = _log()
    signals.add_rule(log, _rule("Slow median", kind="kpi", metric="median_throughput_seconds",
                                operator="above", threshold=3600))
    signals.add_rule(log, _rule("Rework rate", kind="kpi", metric="rework_rate", operator="above", threshold=0.5))
    stuck = signals.add_rule(log, _rule("Open over 2 days", kind="cases", condition="open_longer_than",
                                        seconds=48 * 3600, end_activities=["Close"]))
    signals.add_rule(log, {**_rule("Never closed slow", kind="cases", condition="duration_over", seconds=24 * 3600),
                           "cohort": {"activity": "Close"}})
    first = signals.evaluate(log)
    by_name = {s["name"]: s for s in first["signals"]}
    assert by_name["Slow median"]["triggered"] and by_name["Slow median"]["value"] == pytest.approx(3600 * 6)
    assert by_name["Rework rate"]["value"] == 0.25 and not by_name["Rework rate"]["triggered"]
    open_signal = by_name["Open over 2 days"]
    assert open_signal["triggered"] and open_signal["matching_cases"] == 1  # "late" is recent, "open" is stale
    assert [c["case_id"] for c in open_signal["evidence_cases"]] == ["open"]
    assert open_signal["new_cases_since_last_evaluation"] is None
    assert [c["case_id"] for c in by_name["Never closed slow"]["evidence_cases"]] == ["slow"]
    assert first["triggered"] == 3 and first["signals_execute_actions"] is False
    assert first["execution_authorized"] is False
    again = {s["name"]: s for s in signals.evaluate(log)["signals"]}
    assert again["Open over 2 days"]["new_cases_since_last_evaluation"] == 0
    assert again["Slow median"]["changed_since_last_evaluation"] is False
    listed = signals.list_rules(log)["rules"]
    assert [r["name"] for r in listed][2] == "Open over 2 days" and listed[2]["last_evaluated_at"]
    signals.delete_rule(log, stuck["rule_id"])
    assert len(signals.list_rules(log)["rules"]) == 3


@pytest.mark.parametrize("spec,message", [
    (_rule("x", kind="kpi", metric="sla_breach_rate", operator="above", threshold=0.1), "sla_seconds"),
    (_rule("x", kind="kpi", metric="rework_rate", operator="above", threshold=5), "fractions"),
    (_rule("x", kind="cases", condition="duration_over"), "needs seconds"),
    (_rule("x", kind="cases", condition="contains_activity", seconds=5, activity="A"), "does not use seconds"),
    (_rule("x", kind="cases", condition="open_longer_than", seconds=5), "end a case"),
    (_rule("x", kind="cases", condition="has_rework", end_activities=["Close"]), "end a case"),
    (_rule("x", kind="script", code="rm -rf"), "invalid"),
    ({"name": "x", "when": {"kind": "kpi", "metric": "case_count", "operator": "above", "threshold": 1},
      "execute": "send email"}, "invalid"),
    ({**_rule("x", kind="kpi", metric="case_count", operator="above", threshold=1), "cohort": {"bogus": 1}}, "cohort"),
])
def test_invalid_rules_are_refused(spec, message):
    log = _log()
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        signals.add_rule(log, spec)
    assert signals.list_rules(log)["rules"] == []


def test_rules_are_bounded_scoped_to_their_log_and_deleted_with_it(monkeypatch):
    first, second = _log(), _log()
    rule = signals.add_rule(first, _rule("count", kind="kpi", metric="case_count", operator="above", threshold=1))
    assert signals.list_rules(second)["rules"] == []
    with pytest.raises(workspace.EventLogWorkspaceError):
        signals.delete_rule(second, rule["rule_id"])
    monkeypatch.setattr(signals, "MAX_RULES", 1)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at most"):
        signals.add_rule(first, _rule("again", kind="kpi", metric="case_count", operator="above", threshold=1))
    workspace.delete_log(first)
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown event log"):
        signals.list_rules(first)
    conn = workspace._connect()
    try:
        assert conn.execute("SELECT count(*) FROM signal_rules").fetchone()[0] == 0
    finally:
        conn.close()


def test_new_cases_are_counted_after_the_log_changes():
    log = _log({"a": [("Receive", 0), ("Check", 1)], "z": [("Receive", 200)]})
    signals.add_rule(log, _rule("Missing close", kind="cases", condition="lacks_activity", activity="Close"))
    assert signals.evaluate(log)["signals"][0]["matching_cases"] == 2
    conn = workspace._connect()
    try:  # simulate a re-import keeping the rules: one more unclosed case
        conn.execute("""INSERT INTO events SELECT log_id, 'e_new', 'Receive', ts, resource, attributes
                        FROM events WHERE log_id = ? LIMIT 1""", [log])
        conn.execute("""INSERT INTO objects SELECT log_id, 'b', object_type, attributes FROM objects
                        WHERE log_id = ? LIMIT 1""", [log])
        conn.execute("INSERT INTO event_objects SELECT log_id, 'e_new', 'b', qualifier FROM event_objects WHERE log_id = ? LIMIT 1", [log])
    finally:
        conn.close()
    after = signals.evaluate(log)["signals"][0]
    assert after["matching_cases"] == 3 and after["new_cases_since_last_evaluation"] == 1


def test_real_http_routes_require_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-03T00:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    assert made.status_code == 200, made.text
    base = "/v1/event-logs/" + made.json()["log_id"] + "/signals"
    rule = {"name": "Slow", "when": {"kind": "cases", "condition": "duration_over", "seconds": 3600}}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(base, json=rule, headers=bearer).status_code in (401, 403)
    assert client.post(base + "/evaluate", headers=bearer).status_code in (401, 403)
    created = client.post(base, json=rule, headers=headers)
    assert created.status_code == 200, created.text
    assert client.post(base, content=b"not json", headers={**headers, "content-type": "application/json"}).status_code == 422
    evaluated = client.post(base + "/evaluate", headers=headers).json()
    assert evaluated["signals"][0]["triggered"] and evaluated["signals"][0]["evidence_cases"][0]["case_id"] == "1"
    assert client.get(base, headers=headers).json()["rules"][0]["name"] == "Slow"
    gone = client.post(base + "/" + created.json()["rule_id"] + "/delete", headers=headers)
    assert gone.status_code == 200 and client.get(base, headers=headers).json()["rules"] == []
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
