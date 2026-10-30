"""Process map comparison between two cohorts (roadmap #326 H3)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_map_comparison as compare
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)
JANUARY = {"label": "January", "started_before": "2024-02-01T00:00:00Z"}
FEBRUARY = {"label": "February", "started_from": "2024-02-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _log():
    """January: A→B→C, B after 1 h. February: a new check (A→X→B) in half the cases and B after 3 h."""
    rows = ["case,activity,time"]
    for c in range(60):
        feb = c >= 30
        day = START + timedelta(days=c if not feb else 31 + (c - 30) % 28)
        steps = [("A", 0)] + ([("X", 1)] if feb and c % 2 else []) + [("B", 3 if feb else 1), ("C", 5)]
        rows += [f"c{c},{a},{(day + timedelta(hours=h)).isoformat()}" for a, h in steps]
    return workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="m", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]


def _paths(result):
    return {(p["from"], p["to"]): p for p in result["paths"]}


def test_frequency_and_time_differences_per_path():
    result = compare.compare_maps(_log(), [JANUARY, FEBRUARY])
    assert result["cases"] == [30, 30] and result["overlapping_cases"] == 0
    paths = _paths(result)
    new = paths[("A", "X")]
    assert new["only_in"] == "second" and new["case_share_in_second"] == 0.5 and new["ci_excludes_zero"]
    ab = paths[("A", "B")]
    assert ab["case_share_in_first"] == 1 and ab["case_share_in_second"] == 0.5
    assert result["paths"][0]["ci_excludes_zero"] is True
    bc = paths[("B", "C")]
    assert bc["case_share_difference"] == 0 and not bc["ci_excludes_zero"]
    assert ab["median_gap_seconds_first"] == 3600 and ab["median_gap_seconds_second"] == 3 * 3600
    gap = ab["median_gap_difference"]
    assert gap["seconds"] == 2 * 3600 and gap["ci_excludes_zero"]
    assert (gap["sampled_gaps_first"], gap["sampled_gaps_second"]) == (30, 15)
    assert not gap["sampling_truncated_first"] and not gap["sampling_truncated_second"]
    assert (gap["sampled_median_gap_seconds_first"], gap["sampled_median_gap_seconds_second"]) == (3600, 10800)
    assert gap["seconds"] == gap["sampled_median_gap_seconds_second"] - gap["sampled_median_gap_seconds_first"]
    assert result["paths_only_in_second"] == 2 and result["paths_only_in_first"] == 0



def test_timed_gap_interval_discloses_reservoir_cap_and_sample_medians(monkeypatch):
    """A sampled interval must not silently inherit the full-cohort denominator."""
    monkeypatch.setattr(compare, "GAP_SAMPLE", 4)
    monkeypatch.setattr(compare, "GAP_ROUNDS", 12)
    result = compare.compare_maps(_log(), [JANUARY, FEBRUARY])
    ab = _paths(result)[("A", "B")]
    gap = ab["median_gap_difference"]
    assert (ab["transitions_in_first"], ab["transitions_in_second"]) == (30, 15)
    assert (gap["sampled_gaps_first"], gap["sampled_gaps_second"]) == (4, 4)
    assert gap["sampling_truncated_first"] and gap["sampling_truncated_second"]
    assert gap["seconds"] == gap["sampled_median_gap_seconds_second"] - gap["sampled_median_gap_seconds_first"]
    assert "12 rounds" in gap["method"] and "up to 4 sampled gaps" in gap["method"]
    assert "sampled_gaps_first" not in ab, "sampling support belongs to the timed interval only"
    assert result["intervals_assume_independent_cohorts"] is True

    # The reservoir must operate on the *matching path*, not sample all
    # directly-follows transitions before filtering. A→B and B→C coexist
    # with other paths in both cohorts, but each has >=4 transitions.
    bc = _paths(result)[("B", "C")]
    assert (bc["transitions_in_first"], bc["transitions_in_second"]) == (30, 30)
    bc_gap = bc["median_gap_difference"]
    assert (bc_gap["sampled_gaps_first"], bc_gap["sampled_gaps_second"]) == (4, 4)
    assert bc_gap["sampling_truncated_first"] and bc_gap["sampling_truncated_second"]



def test_side_by_side_maps_and_overlap_warning():
    result = compare.compare_maps(_log(), [{}, FEBRUARY])
    first, second = result["maps"]
    assert first["cases"] == 60 and {a["activity"] for a in second["activities"]} == {"A", "B", "C", "X"}
    assert second["start_activities"] == {"A": 30}
    assert result["overlapping_cases"] == 30 and "overstate" in result["note"]


def test_bad_specs_and_empty_cohorts():
    log = _log()
    for bad in (None, [JANUARY], [JANUARY, FEBRUARY, JANUARY], [JANUARY, {"nope": 1}]):
        with pytest.raises(workspace.EventLogWorkspaceError):
            compare.compare_maps(log, bad)
    with pytest.raises(workspace.EventLogWorkspaceError, match="at least one case"):
        compare.compare_maps(log, [JANUARY, {"started_from": "2030-01-01T00:00:00Z"}])


def test_activity_truncation_distinguishes_exact_cap_from_omitted_names():
    """The 64th activity fills the map; only a 65th is truly omitted."""
    rows = ["case,activity,time"]
    for case_id, day, count in (("exact", START, compare.MAX_MAP_ACTIVITIES),
                                ("overflow", START + timedelta(days=32),
                                 compare.MAX_MAP_ACTIVITIES + 1)):
        for i in range(count):
            stamp = (day + timedelta(minutes=i)).isoformat()
            rows.append(f"{case_id},Activity-{i:03d},{stamp}")
    log = workspace.import_log(
        BytesIO(("\n".join(rows) + "\n").encode()),
        name="boundary", source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time"})["log_id"]

    result = compare.compare_maps(log, [JANUARY, FEBRUARY])
    exact, overflow = result["maps"]
    assert result["cases"] == [1, 1]
    assert len(exact["activities"]) == len(overflow["activities"]) == compare.MAX_MAP_ACTIVITIES
    assert exact["activities_truncated"] is False
    assert overflow["activities_truncated"] is True
    assert [a["activity"] for a in exact["activities"]] == [
        f"Activity-{i:03d}" for i in range(compare.MAX_MAP_ACTIVITIES)]
    assert [a["activity"] for a in overflow["activities"]] == [
        f"Activity-{i:03d}" for i in range(compare.MAX_MAP_ACTIVITIES)]
    assert "Activity-064" not in {a["activity"] for a in overflow["activities"]}
    # Existing displayed-edge and start-activity limits remain unchanged.
    assert len(exact["transitions"]) == len(overflow["transitions"]) == compare.MAX_MAP_ACTIVITIES - 1
    assert exact["start_activities"] == overflow["start_activities"] == {"Activity-000": 1}


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = b"case,activity,time\n1,A,2024-01-01T00:00:00Z\n1,B,2024-01-01T01:00:00Z\n2,A,2024-03-01T00:00:00Z\n2,C,2024-03-01T01:00:00Z\n"
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/map-comparison"
    body = {"cohorts": [{"started_before": "2024-02-01T00:00:00Z"}, {"started_from": "2024-02-01T00:00:00Z"}]}
    assert client.post(path, json=body, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.post(path, json=body, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert {p["only_in"] for p in result.json()["paths"]} == {"first", "second"}
    assert client.post(path, json={"cohorts": []}, headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_events_compare_within_budget():
    import time
    rows = ["case,activity,time"]
    for c in range(200_000):
        for s in range(5):
            rows.append(f"c{c},S{(s + c % 7) % 12},{(START + timedelta(seconds=c * 30 + s * 5)).isoformat()}")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})["log_id"]
    started = time.perf_counter()
    result = compare.compare_maps(log, [{"started_before": "2024-01-02T00:00:00Z"}, {"started_from": "2024-01-02T00:00:00Z"}])
    assert time.perf_counter() - started < 30 and result["paths_total"] >= 10
