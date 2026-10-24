"""Declarative compliance rules over saved event logs (roadmap #326 G2)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_compliance as compliance
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 3, 1, tzinfo=timezone.utc)
# (activity, hours after case start, resource)
CASES = {
    "c1": [("Create PO", 0, "ann"), ("Approve PO", 1, "bob"), ("Receive", 2, "dan"), ("Pay", 3, "carl")],
    "c2": [("Create PO", 0, "ann"), ("Approve PO", 0.5, "ann"), ("Pay", 1, "carl")],
    "c3": [("Approve PO", 0, "bob"), ("Create PO", 1, "ann"), ("Pay", 2, "carl")],
    "c4": [("Create PO", 0, "ann"), ("Pay", 1, "carl"), ("Pay", 2, "carl")],
    "c5": [("Create PO", 0, "ann"), ("Approve PO", 5, "")],
    "c6": [("Receive", 0, "dan")],
}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(cases=CASES):
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["case", "activity", "time", "who"])
    for n, (case, steps) in enumerate(cases.items()):
        for activity, hours, who in steps:
            writer.writerow([case, activity, (START + timedelta(days=n, hours=hours)).isoformat(), who])
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="p2p", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who"})["log_id"]


def _run(*rules, **extra):
    result = compliance.check(_log(), {"rules": list(rules), **extra})
    return result, result["results"]


def _violating(summary):
    return [e["case_id"] for e in summary["violating_case_examples"]]


def test_each_template_finds_exactly_the_violating_cases():
    result, (response, precedence, within, sod, once, never_both) = _run(
        {"template": "response", "a": "Create PO", "b": "Approve PO"},
        {"template": "precedence", "a": "Create PO", "b": "Approve PO"},
        {"template": "response_within", "a": "Create PO", "b": "Approve PO", "seconds": 7200},
        {"template": "segregation_of_duties", "a": "Create PO", "b": "Approve PO"},
        {"template": "existence", "a": "Pay", "min": 0, "max": 1},
        {"template": "not_coexistence", "a": "Receive", "b": "Approve PO"},
    )
    assert _violating(response) == ["c3", "c4"]          # c3 approves before it creates; c4 never approves
    assert response["activated_cases"] == 5 and response["vacuously_satisfied_cases"] == 1
    assert _violating(precedence) == ["c3"]
    assert _violating(within) == ["c3", "c4", "c5"]       # c5 approves after 5 h
    assert _violating(sod) == ["c2"] and sod["unknown_cases"] == 1  # c5 has no approver recorded
    assert sod["satisfied_cases"] == 2 and sod["violation_rate"] == pytest.approx(1 / 3)
    assert _violating(once) == ["c4"]
    assert _violating(never_both) == ["c1"]
    assert result["rules_with_violations"] == 6 and result["resource_names_returned"] is False
    lo, hi = response["violation_rate_ci95"]
    assert lo < response["violation_rate"] == 0.4 < hi
    assert "ann" not in json.dumps(result) and "bob" not in json.dumps(result)
    assert response["description"] == "every Create PO is eventually followed by Approve PO"


def test_ties_follow_event_order_and_repeats_check_the_last_trigger():
    cases = {"t1": [("A", 0, "x"), ("B", 0, "y")],          # same timestamp: file order decides
             "t2": [("A", 0, "x"), ("B", 1, "y"), ("A", 2, "x")],  # second A never answered
             "t3": [("A", 0, "x"), ("A", 1, "x"), ("B", 2, "y")]}
    summary = compliance.check(_log(cases), {"rules": [{"template": "response", "a": "A", "b": "B"}]})["results"][0]
    assert _violating(summary) == ["t2"]


def test_cohorts_and_minimum_existence_apply():
    result, (must_pay,) = _run({"template": "existence", "a": "Pay", "min": 1}, cohort={"activity": "Create PO"})
    assert must_pay["cases"] == 5 and _violating(must_pay) == ["c5"]


@pytest.mark.parametrize("spec,message", [
    ({"rules": [{"template": "response", "a": "A", "b": "A"}]}, "two different"),
    ({"rules": [{"template": "existence", "a": "A"}]}, "min above 0"),
    ({"rules": [{"template": "existence", "a": "A", "min": 3, "max": 1}]}, "below min"),
    ({"rules": [{"template": "response_within", "a": "A", "b": "B"}]}, "seconds"),
    ({"rules": [{"template": "eventually_ok", "a": "A"}]}, "invalid"),
    ({"rules": [{"template": "response", "a": "A", "b": "B", "sql": "x"}]}, "invalid"),
    ({"rules": []}, "invalid"),
    ({"rules": [{"template": "response", "a": "A", "b": "B"}], "cohort": {"bogus": 1}}, "cohort"),
])
def test_invalid_rule_sets_are_refused(spec, message):
    with pytest.raises(workspace.EventLogWorkspaceError, match=message):
        compliance.check(_log(), spec)


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time,who\n1,Create,2024-01-01T00:00:00Z,ann\n1,Approve,2024-01-01T01:00:00Z,ann\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&resource_column=who", content=doc,
                       headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/compliance"
    rules = {"rules": [{"template": "segregation_of_duties", "a": "Create", "b": "Approve"}]}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    assert client.post(path, json=rules, headers=bearer).status_code in (401, 403)
    result = client.post(path, json=rules, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["results"][0]["violating_cases"] == 1 and "ann" not in result.text
    assert client.post(path, json={"rules": []}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_with_six_rules_stay_interactive():
    import time
    out = StringIO()
    out.write("case,activity,time,who\n")
    for c in range(250_000):
        for s, name in enumerate(("Create PO", "Approve PO", "Receive", "Pay")):
            out.write(f"c{c},{name},{(START + timedelta(seconds=c * 10 + s)).isoformat()},r{(c + s) % 40}\n")
    log = workspace.import_log(BytesIO(out.getvalue().encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})["log_id"]
    rules = [{"template": t, "a": "Create PO", "b": "Approve PO"} for t in
             ("response", "precedence", "segregation_of_duties", "not_coexistence")]
    rules += [{"template": "response_within", "a": "Create PO", "b": "Approve PO", "seconds": 60},
              {"template": "existence", "a": "Pay", "min": 1, "max": 1}]
    started = time.perf_counter()
    result = compliance.check(log, {"rules": rules})
    assert time.perf_counter() - started < 60 and result["results"][0]["cases"] == 250_000


def test_compliance_rules_can_be_saved_as_signals_and_track_new_violations():
    from server import event_log_signals as signals
    log = _log()
    signals.add_rule(log, {"name": "Four eyes on POs", "when": {"kind": "compliance", "rule": {
        "template": "segregation_of_duties", "a": "Create PO", "b": "Approve PO"}}})
    first = signals.evaluate(log)["signals"][0]
    assert first["triggered"] and first["matching_cases"] == 1 and first["evidence_cases"][0]["case_id"] == "c2"
    assert first["compliance"]["unknown_cases"] == 1 and first["new_cases_since_last_evaluation"] is None
    newer = b"case,activity,time,who\nc9,Create PO,2024-04-01T00:00:00Z,eve\nc9,Approve PO,2024-04-01T01:00:00Z,eve\n"
    workspace.append_log(log, BytesIO(newer), source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time", "resource_column": "who"})
    again = signals.evaluate(log)["signals"][0]
    assert again["matching_cases"] == 2 and again["new_cases_since_last_evaluation"] == 1
    with pytest.raises(workspace.EventLogWorkspaceError, match="two different"):
        signals.add_rule(log, {"name": "bad", "when": {"kind": "compliance", "rule": {
            "template": "response", "a": "Pay", "b": "Pay"}}})
