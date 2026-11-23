"""Decision mining learns which recorded data goes with which branch after a decision activity."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_decision_mining as decisions
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
           "resource_column": "who", "attribute_columns": ["amount", "channel", "late_note"]}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log(n: int = 200):
    # After "Check", invoices of 5000 or more go to "Manager approval", the rest to "Auto approve".
    # "late_note" is recorded only after the decision and matches the branch perfectly: it must never be used.
    rows = ["case,activity,time,who,amount,channel,late_note"]
    for i in range(n):
        amount = (i * 137) % 10000
        channel = "email" if i % 3 else "portal"
        branch = "Manager approval" if amount >= 5000 else "Auto approve"
        rows += [f"c{i},Receive,2024-03-01T08:00:00Z,mail,{amount},{channel},",
                 f"c{i},Check,2024-03-01T09:00:00Z,ana,,,",
                 f"c{i},{branch},2024-03-01T10:00:00Z,bob,,,{'M' if amount >= 5000 else 'A'}",
                 f"c{i},Pay,2024-03-01T11:00:00Z,bot,,,"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="invoices", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_literal_end_activity_and_case_completion_are_separate_branches():
    rows = ["case,activity,time,amount"]
    for i in range(200):
        amount = i % 2
        rows.append(f"c{i},Check,2024-03-01T08:00:00Z,{amount}")
        if amount:
            rows.extend([f"c{i},(end),2024-03-01T09:00:00Z,",
                         f"c{i},(case ended),2024-03-01T10:00:00Z,"])
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="completion branches", source_format="csv",
        mapping={**MAPPING, "resource_column": "", "attribute_columns": ["amount"]})["log_id"]
    points = decisions.decision_points(log)
    terminal = points["terminal_label"]
    assert terminal == "(case ended 2)"
    point = next(point for point in points["decision_points"] if point["activity"] == "Check")
    assert {(b["next"], b["next_is_terminal"], b["occurrences"]) for b in point["branches"]} == {
        ("(end)", False, 100), (terminal, True, 100),
    }
    result = decisions.analyse(log, "Check")
    assert result["terminal_label"] == terminal
    assert result["evaluation"]["holdout_accuracy"] == 1
    assert {(r["next"], r["next_is_terminal"]) for r in result["rules"]} == {
        ("(end)", False), (terminal, True),
    }
    assert set(result["evaluation"]["holdout_confusion"]) == {"(end)", terminal}
    literal = decisions.branch_cases(log, selection={"activity": "Check", "next_activity": "(end)"})
    ended = decisions.branch_cases(log, selection={"activity": "Check", "ends_here": True})
    assert literal["total"] == ended["total"] == 100
    assert literal["cases"][0] == {"case_id": "c1", "occurrences": 1}
    assert ended["cases"][0] == {"case_id": "c0", "occurrences": 1}
    assert decisions.branch_cases(log, selection={"activity": "Check", "next_activity": terminal})["total"] == 0


def test_observed_branch_pages_keep_case_counts_and_occurrences_distinct():
    rows = ["case,activity,time"]
    for key, path in {"c1": ["Check", "Review", "Check", "Review"],
                      "c2": ["Start", "Check", "Review"],
                      "c10": ["Check", "Review", "Other"],
                      "c11": ["Start", "Check", "Other"]}.items():
        for hour, activity in enumerate(path):
            rows.append(f"{key},{activity},2024-03-01T{hour:02d}:00:00Z")
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="repeated reviews", source_format="csv",
                               mapping={k: MAPPING[k] for k in ("case_column", "activity_column", "timestamp_column")}
                               )["log_id"]
    selection = {"activity": "Check", "next_activity": "Review", "limit": 2}
    first = decisions.branch_cases(log, selection=selection)
    second = decisions.branch_cases(log, selection={**selection, "after": first["next_after"]})
    assert first["total"] == second["total"] == 3
    assert first["occurrences"] == second["occurrences"] == 4
    assert first["cases"] == [{"case_id": "c1", "occurrences": 2}, {"case_id": "c10", "occurrences": 1}]
    assert second["cases"] == [{"case_id": "c2", "occurrences": 1}]
    assert second["next_after"] is None
    filtered = decisions.branch_cases(log, selection=selection, cohort={"activity": "Start"})
    assert filtered["total"] == filtered["occurrences"] == 1
    assert filtered["cases"] == second["cases"]


@pytest.mark.parametrize("selection", [
    "Check", {}, {"activity": "Check"}, {"activity": "Check", "ends_here": False},
    {"activity": "Check", "ends_here": True, "next_activity": "(end)"},
    {"activity": "Check", "ends_here": True, "limit": True},
    {"activity": "Check", "ends_here": True, "after": ""},
])
def test_observed_branch_rejects_invalid_selection(selection):
    with pytest.raises(EventLogWorkspaceError):
        decisions.branch_cases("unused", selection=selection)


def test_decision_points_list_branches():
    log = _log()
    result = decisions.decision_points(log)
    by = {p["activity"]: p for p in result["decision_points"]}
    assert set(by) == {"Check"}
    branches = {b["next"]: b["occurrences"] for b in by["Check"]["branches"]}
    assert set(branches) == {"Manager approval", "Auto approve"} and sum(branches.values()) == 200


def test_rule_recovers_threshold_without_later_data():
    log = _log()
    result = decisions.analyse(log, "Check")
    assert result["learned_anything"] is True
    used = {f["attribute"] for f in result["feature_importance"]}
    assert "event.late_note" not in used and "event.late_note" not in {f["name"] for f in result["features"]}
    assert result["feature_importance"][0]["attribute"] == "event.amount"
    top = result["rules"][0]
    assert top["conditions"][0]["attribute"] == "event.amount"
    threshold = top["conditions"][0]["value"]
    assert 4900 <= threshold <= 5100
    evaluation = result["evaluation"]
    assert evaluation["holdout_accuracy"] == 1.0 and evaluation["beats_baseline"] is True
    assert evaluation["training_occurrences"] + evaluation["holdout_occurrences"] == 200
    assert "not the business rule" in result["caveat"]


def test_split_is_repeatable_and_case_level():
    log = _log()
    assert decisions.analyse(log, "Check")["rules"] == decisions.analyse(log, "Check")["rules"]


def test_no_decision_and_validation():
    log = _log()
    result = decisions.analyse(log, "Receive")
    assert result["rules"] == [] and "no decision" in result["note"]
    with pytest.raises(EventLogWorkspaceError):
        decisions.analyse(log, "Nope")
    with pytest.raises(EventLogWorkspaceError):
        decisions.analyse(log, "Check", max_depth=9)
    with pytest.raises(EventLogWorkspaceError):
        decisions.analyse(log, "")


def test_cohort_filter_applies():
    log = _log()
    result = decisions.analyse(log, "Check", cohort={"event_attribute": "channel=portal"})
    assert result["occurrences"] < 200


def test_routes_are_dashboard_only(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time,amount"]
for i in range(60):
    amount = i * 100
    rows += [f"c{i},Check,2024-03-01T08:00:00Z,{amount}", f"c{i},{'Big' if amount >= 3000 else 'Small'},2024-03-01T09:00:00Z,"]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time&attribute_column=amount",
                         content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"}).json()["log_id"]
    points = f"/v1/event-logs/{log_id}/decision-points"
    rules = f"/v1/event-logs/{log_id}/decision-rules"
    assert client.get(points, headers=bearer).status_code == 403
    assert client.get(rules + "?decision_activity=Check", headers=bearer).status_code == 403
    r = client.get(points, headers=owner)
    assert r.status_code == 200 and [p["activity"] for p in r.json()["decision_points"]] == ["Check"], r.text
    r = client.get(rules + "?decision_activity=Check&max_depth=2", headers=owner)
    assert r.status_code == 200 and r.json()["rules"][0]["conditions"][0]["attribute"] == "event.amount", r.text
    assert client.get(rules + "?decision_activity=Check&max_depth=5", headers=owner).status_code == 422
    assert client.get(rules, headers=owner).status_code == 422
    # The case filter's own activity parameter still filters cases, independently of the decision.
    r = client.get(rules + "?decision_activity=Check&activity=Big", headers=owner)
    assert r.status_code == 200 and r.json()["occurrences"] == 30 and r.json()["cases_before_filter"] == 60, r.text
    assert client.get(rules + "?decision_activity=Nope", headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "srvauth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    root = Path(__file__).resolve().parents[1]
    done = subprocess.run([sys.executable, "-c", code], cwd=root, env=env, capture_output=True, text=True, timeout=180)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


@pytest.mark.parametrize("low,high", [("0.00000001", "0.00000002"), ("1e308", "1.1e308"),
                                     ("1.0", "1.0000000000000002")])
def test_numeric_threshold_reproduces_scored_partition(low, high):
    import math

    rows = ["case,activity,time,amount"]
    for i in range(200):
        value, target = (low, "Small") if i % 2 else (high, "Big")
        rows.extend([f"c{i},Check,2024-03-01T08:00:00Z,{value}",
                     f"c{i},{target},2024-03-01T09:00:00Z,"])
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="numeric boundary", source_format="csv",
        mapping={**MAPPING, "resource_column": "", "attribute_columns": ["amount"]})["log_id"]
    result = decisions.analyse(log, "Check")
    assert result["learned_anything"] and result["evaluation"]["holdout_accuracy"] == 1
    rules = {rule["next"]: rule for rule in result["rules"]}
    assert rules.keys() == {"Big", "Small"}
    threshold = rules["Big"]["conditions"][0]["value"]
    assert math.isfinite(threshold) and float(low) < threshold <= float(high)
    assert rules["Small"]["conditions"] == [{"attribute": "event.amount", "op": "<", "value": threshold,
                                             "includes_missing": True, "includes_non_numeric": True}]
    assert min(rule["training_occurrences"] for rule in rules.values()) >= result["min_leaf_occurrences"]
