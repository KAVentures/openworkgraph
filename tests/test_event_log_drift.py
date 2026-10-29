"""Concept drift detection over time windows (roadmap #326 H4)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_drift as drift
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log(changed_from_month=3, months=5, per_month=60):
    """Every month the same mix, until a new approval step replaces the old check."""
    rows = ["case,activity,time"]
    for m in range(months):
        for c in range(per_month):
            day = datetime(2024, m + 1, 1 + c % 27, tzinfo=timezone.utc)
            if m >= changed_from_month:
                steps = ["Receive", "Approve", "Close"] if c % 4 else ["Receive", "Check", "Close"]
            else:
                steps = ["Receive", "Check", "Close"] if c % 3 else ["Receive", "Check", "Fix", "Close"]
            rows += [f"m{m}c{c},{a},{(day + timedelta(hours=h)).isoformat()}" for h, a in enumerate(steps)]
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="d", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def test_a_real_change_is_found_and_stable_months_are_not():
    result = drift.drift(_log())
    assert [w["window"] for w in result["windows"]] == ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"]
    assert result["boundaries_tested"] == 4
    points = result["change_points"]
    assert [(p["before"], p["after"]) for p in points] == [("2024-03-01", "2024-04-01")]
    moved = {m["path"]: m for m in points[0]["paths_that_moved"]}
    assert moved["Receive → Approve"]["case_share_before"] == 0 and moved["Receive → Approve"]["case_share_after"] == 0.75
    assert moved["Receive → Approve"]["from"] == "Receive" and moved["Receive → Approve"]["to"] == "Approve"
    stable = [b for b in result["boundaries"] if not b["change_point"]]
    assert all(b["p_value"] > 0.05 / 4 for b in stable) and all(b["distance"] < points[0]["distance"] for b in stable)



def test_activity_names_containing_arrow_do_not_collide_as_drift_edges():
    """Different endpoint pairs must not become the same flattened path."""
    rows = ["case,activity,time"]
    for month, first, second in ((1, "A → B", "C"), (2, "A", "B → C")):
        for i in range(60):
            day = datetime(2024, month, 1 + i % 27, tzinfo=timezone.utc)
            rows.append(f"m{month}c{i},{first},{day.isoformat()}")
            rows.append(f"m{month}c{i},{second},{(day + timedelta(hours=1)).isoformat()}")
    log = workspace.import_log(
        BytesIO(("\n".join(rows) + "\n").encode()), name="arrow-paths",
        source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time"})["log_id"]

    result = drift.drift(log, window="month")
    assert result["boundaries_tested"] == 1
    assert len(result["change_points"]) == 1
    change = result["change_points"][0]
    assert change["distance"] > 0.9
    moved = {(p["from"], p["to"]): p for p in change["paths_that_moved"]}
    assert set(moved) == {("A → B", "C"), ("A", "B → C")}
    assert moved[("A → B", "C")]["case_share_before"] == 1
    assert moved[("A → B", "C")]["case_share_after"] == 0
    assert moved[("A", "B → C")]["case_share_before"] == 0
    assert moved[("A", "B → C")]["case_share_after"] == 1
    assert len({p["path"] for p in moved.values()}) == 2
    # The legacy display remains readable for simple labels; ambiguous labels
    # receive a reversible JSON-pair display, with explicit endpoints in both.
    assert all(p["path"].startswith('["') for p in moved.values())


def test_window_sampling_provenance_at_and_above_cap():
    """Report the exact dcase denominator, not the full case count."""
    cap = drift.SAMPLE_PER_WINDOW
    rows = ["case,activity,time"]
    for month, count in ((1, cap), (2, cap + 1)):
        for i in range(count):
            day = datetime(2024, month, 1 + i % 27, tzinfo=timezone.utc)
            case = f"m{month}c{i}"
            rows.append(f"{case},Receive,{day.isoformat()}")
            rows.append(f"{case},Close,{(day + timedelta(hours=1)).isoformat()}")
    log = workspace.import_log(
        BytesIO(("\n".join(rows) + "\n").encode()), name="sample-cap",
        source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time"})["log_id"]

    result = drift.drift(log, window="month")
    assert result["settings"]["sample_per_window"] == cap
    assert [(w["cases"], w["sampled_cases"], w["sampling_truncated"])
            for w in result["windows"]] == [(cap, cap, False), (cap + 1, cap, True)]
    assert result["boundaries_tested"] == 1
    # Both samples contain the same directly-follows path. Sampling alone
    # must not manufacture a change point.
    assert result["change_points"] == []


def test_no_change_means_no_change_points():
    result = drift.drift(_log(changed_from_month=99))
    assert result["change_points"] == [] and result["boundaries_tested"] == 4


def test_case_count_windows_small_windows_and_validation():
    log = _log()
    by_cases = drift.drift(log, window="cases", window_cases=60)
    assert len(by_cases["windows"]) == 5 and by_cases["windows"][0]["window"] == "cases 1–60"
    assert [(p["before"], p["after"]) for p in by_cases["change_points"]] == [("cases 121–180", "cases 181–240")]
    weekly = drift.drift(log, window="week", min_cases=1000)
    assert weekly["boundaries_tested"] == 0 and weekly["boundaries_skipped_small_windows"] > 0
    for bad in ({"window": "day"}, {"window_cases": 5}, {"min_cases": 1}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            drift.drift(log, **bad)
    monkey = drift.MAX_WINDOWS
    try:
        drift.MAX_WINDOWS = 3
        with pytest.raises(workspace.EventLogWorkspaceError, match="longer window"):
            drift.drift(log)
    finally:
        drift.MAX_WINDOWS = monkey


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
rows = ["case,activity,time"] + [f"{m}{c},A,2024-0{m}-{1 + c % 27:02d}T00:00:00Z" for m in (1, 2) for c in range(40)]
rows += [f"{m}{c},{'B' if m == 1 else 'C'},2024-0{m}-{1 + c % 27:02d}T01:00:00Z" for m in (1, 2) for c in range(40)]
doc = ("\n".join(rows) + "\n").encode()
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/drift"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path + "?window=month", headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert len(result.json()["change_points"]) == 1
    assert client.get(path + "?window=year", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_within_budget():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        day = START + timedelta(minutes=c * 2)
        late = c > 120_000
        for s in range(5):
            rows.append(f"c{c},S{(s + (c % 3 if not late else c % 5)) % 9},{(day + timedelta(seconds=s)).isoformat()}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    started = time.perf_counter()
    result = drift.drift(log, window="week")
    assert time.perf_counter() - started < 60 and result["change_points"]
