"""Next-activity prediction for open cases."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import BytesIO, StringIO

import pytest

from server import event_log_next_activity as nxt
from server import event_log_workspace as workspace

START = datetime(2024, 1, 1, 8, tzinfo=timezone.utc)
MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(paths):
    rows = []
    for i, path in enumerate(paths):
        for k, activity in enumerate(path):
            rows.append([f"c{i:03d}", activity, (START + timedelta(days=i, hours=k)).isoformat()])
    out = StringIO()
    w = csv.writer(out)
    w.writerow(["case", "activity", "time"])
    w.writerows(rows)
    return workspace.import_log(BytesIO(out.getvalue().encode()), name="n", source_format="csv", mapping=MAPPING)["log_id"]


def _paths():
    """After Check, a case that started with Receive goes to Approve; one that started with Import goes to Reject.
    Only a two-step history can tell them apart. The last four cases are still open."""
    paths = []
    for i in range(40):
        paths.append(["Receive", "Check", "Approve", "Pay"] if i % 2 == 0 else ["Import", "Check", "Reject", "Close"])
    paths += [["Receive", "Check"], ["Import", "Check"], ["Receive"], ["Import", "Check", "Reject"]]
    return paths


def test_open_cases_get_the_history_specific_next_activity():
    log = _log(_paths())
    r = nxt.next_activity(log, {"end_activities": ["Pay", "Close"], "min_support": 3})
    assert r["format"] == nxt.FORMAT and r["completed_cases"] == 40 and r["open_cases"] == 4
    by = {p["case_id"]: p for p in r["predictions"]}
    assert by["c040"]["next"][0] == {"activity": "Approve", "case_ends": False, "probability": 1.0}
    assert by["c040"]["history_used"] == 2 and by["c040"]["support"] == 20
    assert by["c041"]["next"][0]["activity"] == "Reject"
    assert by["c042"]["history_used"] == 1 and by["c042"]["next"][0]["activity"] == "Check"
    assert by["c043"]["next"][0]["activity"] == "Close"
    # Most recent open case first.
    assert [p["case_id"] for p in r["predictions"]] == ["c043", "c042", "c041", "c040"]
    expected = {e["activity"]: e["expected_cases"] for e in r["expected_next_across_open_cases"]}
    assert expected == {"Approve": 1.0, "Reject": 1.0, "Check": 1.0, "Close": 1.0}
    b = r["backtest"]
    assert b["available"] and b["train_cases"] == 28 and b["test_cases"] == 12
    # Two-step history is exact here: every step is predictable, including the case ending.
    assert b["top1_accuracy"] == 1.0 and b["baseline_current_activity_only"] < 1.0
    assert b["baseline_most_frequent_next"] < b["top1_accuracy"]
    assert "not instructions" in r["interpretation"]


def test_history_one_cannot_separate_the_two_routes():
    log = _log(_paths())
    r = nxt.next_activity(log, {"end_activities": ["Pay", "Close"], "history": 1, "top": 2, "min_support": 3})
    c040 = [p for p in r["predictions"] if p["case_id"] == "c040"][0]
    assert [n["probability"] for n in c040["next"]] == [0.5, 0.5]
    assert r["backtest"]["top1_accuracy"] < 1.0


def test_exact_case_outside_the_preview_does_not_change_history_or_forecast_totals():
    log = _log(_paths())
    spec = {"end_activities": ["Pay", "Close"], "limit": 1}
    preview = nxt.next_activity(log, spec)
    assert preview["predictions"][0]["case_id"] == "c043"
    exact = nxt.next_activity(log, spec, case_id="c040")
    assert exact["case_selection"] == "predicted"
    assert exact["predictions"][0]["case_id"] == "c040"
    assert exact["predictions"][0]["next"][0]["activity"] == "Approve"
    assert exact["backtest"] == preview["backtest"]
    assert exact["expected_next_across_open_cases"] == preview["expected_next_across_open_cases"]
    excluded = nxt.next_activity(log, {**spec, "cohort": {"activity": "Import"}}, case_id="c040")
    assert excluded["case_selection"] == "not_in_selection" and not excluded["predictions"]
    assert nxt.next_activity(log, spec, case_id="c000")["case_selection"] == "completed"
    assert nxt.next_activity(log, spec, case_id="C040")["case_selection"] == "not_in_selection"
    no_history = nxt.next_activity(log, {"end_activities": ["Other"]}, case_id="c040")
    assert no_history["case_selection"] == "unsupported" and not no_history["predictions"]


def test_backoff_case_ending_and_errors():
    log = _log(_paths())
    # With a high support bar, two-step histories (20 cases each) fall back to all transitions.
    r = nxt.next_activity(log, {"end_activities": ["Pay", "Close"], "min_support": 1000, "limit": 1})
    assert r["predictions"][0]["history_used"] == 0 and len(r["predictions"]) == 1 and r["open_cases_shown"] == 1
    ending = nxt.next_activity(log, {"end_activities": ["Approve", "Reject"], "min_support": 1})
    # Completion is judged by the latest activity only: just the "Import, Check, Reject" case ends with one.
    assert ending["completed_cases"] == 1
    for bad in (None, {}, {"end_activities": []}, {"end_activities": ["Pay"], "history": 0},
                {"end_activities": ["Pay"], "history": 5}, {"end_activities": ["Pay"], "backtest_share": 1},
                {"end_activities": ["Pay"], "x": 1}, {"end_activities": ["Nothing"]},
                {"end_activities": ["Pay"], "cohort": {"nope": 1}}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            nxt.next_activity(log, bad)


def test_small_logs_report_that_the_backtest_is_unavailable():
    log = _log([["A", "B"]] * 6 + [["A"]])
    r = nxt.next_activity(log, {"end_activities": ["B"], "min_support": 1})
    assert r["backtest"]["available"] is False and "at least 10" in r["backtest"]["reason"]
    assert r["predictions"][0]["next"][0] == {"activity": "B", "case_ends": False, "probability": 1.0}


def test_training_evidence_pages_match_the_prediction_support_and_exact_successors():
    log = _log(_paths())
    settings = {"end_activities": ["Pay", "Close"], "cohort": {"activity": "Receive"}}
    result = nxt.next_activity(log, settings, case_id="c040", include_evidence=True)
    [estimate] = result["predictions"]
    assert estimate["matched_history"] == ["Receive", "Check"]
    request = {"settings": settings, "case_id": "c040", "expected_snapshot": result["evidence_snapshot"], "limit": 7}
    after, all_rows = None, []
    while True:
        page = nxt.training_evidence(log, {**request, "after": after})
        assert page["support"] == estimate["support"] == 20
        assert page["matched_history"] == estimate["matched_history"]
        all_rows.extend(page["occurrences"])
        after = page["next_after"]
        if after is None:
            break
    assert all_rows == [{"case_id": f"c{i:03d}", "step": 2, "activity": "Check", "next_activity": "Approve", "case_ends": False}
                        for i in range(0, 40, 2)]
    for target in ("c000", "c041"):
        with pytest.raises(workspace.EventLogWorkspaceError, match="predicted open case"):
            nxt.training_evidence(log, {**request, "case_id": target})


def test_training_evidence_preserves_repeated_and_terminal_steps_and_refuses_stale_projection():
    log = _log([["Check", "Check", "Close"], ["Check"]])
    settings = {"end_activities": ["Close"], "min_support": 10}
    result = nxt.next_activity(log, settings, include_evidence=True)
    request = {"settings": settings, "case_id": "c001", "expected_snapshot": result["evidence_snapshot"]}
    page = nxt.training_evidence(log, request)
    assert page["matched_history"] == [] and page["support"] == 3
    assert [(r["step"], r["activity"], r["next_activity"], r["case_ends"]) for r in page["occurrences"]] == [
        (1, "Check", "Check", False), (2, "Check", "Close", False), (3, "Close", None, True)]
    first = nxt.training_evidence(log, {**request, "limit": 1})
    following = nxt.training_evidence(log, {**request, "after": first["next_after"]})
    assert first["occurrences"] + following["occurrences"] == page["occurrences"]
    workspace.append_log(log, BytesIO(b"case,activity,time\nc001,Close,2024-01-31T01:00:00Z\n"),
                         source_format="csv", mapping=MAPPING)
    with pytest.raises(workspace.EventLogWorkspaceError, match="prediction history changed"):
        nxt.training_evidence(log, request)


def test_route_requires_the_dashboard(tmp_path):
    import os
    import subprocess
    import sys
    from pathlib import Path
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"c{i},A,2024-03-04T08:00:0{i}Z" for i in range(4)] + [f"c{i},B,2024-03-04T09:00:0{i}Z" for i in range(3)]
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    imported = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                           content="\n".join(rows).encode(), headers={**owner, "X-OWG-Local-Storage-Confirmed": "true"})
    assert imported.status_code == 200, imported.text
    path = f"/v1/event-logs/{imported.json()['log_id']}/next-activity"
    body = {"end_activities": ["B"], "min_support": 1}
    assert client.post(path, json=body, headers=bearer).status_code == 403
    found = client.post(path, json=body, headers=owner)
    assert found.status_code == 200, found.text
    assert found.json()["predictions"][0]["next"][0]["activity"] == "B"
    training = {"settings": body, "case_id": "c3", "expected_snapshot": found.json()["evidence_snapshot"], "limit": 1}
    assert client.post(path + "/training-evidence", json=training, headers=bearer).status_code == 403
    page = client.post(path + "/training-evidence", json=training, headers=owner)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store", page.text
    assert page.json()["support"] == 3 and page.json()["next_after"] == ["c0", 1]
    assert client.post(path + "/training-evidence", json={**training, "expected_snapshot": "0"*64}, headers=owner).status_code == 422
    selection = {**body, "case_id": "c3"}
    assert client.post(path, json=selection, headers=bearer).status_code == 403
    assert client.post(path, json=selection, headers=owner).json()["case_selection"] == "predicted"
    assert client.post(path, json={**body, "case_id": ""}, headers=owner).status_code == 422
    assert client.post(path, json={"end_activities": []}, headers=owner).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"
