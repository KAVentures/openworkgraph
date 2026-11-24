"""Processing and waiting time from lifecycle or end-time data (roadmap #326 H1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import event_log_waiting as waiting
from server import event_log_workspace as workspace

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _at(day, hours):
    return (START + timedelta(days=day, hours=hours)).isoformat()


def _import(header, rows):
    text = "\n".join([",".join(header)] + [",".join(map(str, r)) for r in rows]) + "\n"
    return workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": header[3:]})["log_id"]


def _lifecycle_log():
    rows = []
    for c in range(10):   # Receive 0–1 h, wait 2 h, Review 3–5 h, Close 5–5.5 h
        for activity, start, end in (("Receive", 0, 1), ("Review", 3, 5), ("Close", 5, 5.5)):
            rows += [(f"c{c}", activity, _at(c, start), "start"), (f"c{c}", activity, _at(c, end), "complete")]
    rows += [("c10", "Receive", _at(10, 0), "complete"),          # no start recorded
             ("c10", "Review", _at(10, 1), "start"), ("c10", "Review", _at(10, 2), "complete"),
             ("c11", "Receive", _at(11, 0), "schedule"),          # ignored lifecycle value
             ("c11", "Receive", _at(11, 0), "start"), ("c11", "Receive", _at(11, 1), "complete"),
             ("c11", "Review", _at(11, 2), "start")]              # still running
    return _import(["case", "activity", "time", "lifecycle:transition"], rows)


def _by(result):
    return {a["activity"]: a for a in result["activities"]}


@pytest.mark.parametrize("bad_attribute", [None, 12, [], {}, "bad\\nname", "bad\\\"name"])
def test_invalid_lifecycle_attribute_is_rejected_as_workspace_error(bad_attribute):
    log = _lifecycle_log()
    with pytest.raises(workspace.EventLogWorkspaceError, match="Attribute names"):
        waiting.waiting_time(log, lifecycle_attribute=bad_attribute)


def test_lifecycle_pairs_split_processing_from_waiting():
    result = waiting.waiting_time(_lifecycle_log())
    assert result["available"] and result["mode_used"] == "lifecycle"
    acts = _by(result)
    assert acts["Receive"]["processing"]["median_seconds"] == 3600
    assert acts["Review"]["processing"]["median_seconds"] == 7200
    assert acts["Review"]["waiting_before"]["median_seconds"] == 7200
    assert acts["Review"]["waiting_before"]["p10_seconds"] == 7200
    assert acts["Review"]["waiting_before"]["population_stddev_seconds"] > 0
    assert acts["Review"]["waiting_before"]["instances"] == 11
    assert acts["Review"]["open_starts"] == 1
    # c11 has only a Review start; it still belongs in Review's case count.
    assert acts["Review"]["cases"] == 12
    assert acts["Review"]["processing_observation_share"] == pytest.approx(11 / 12)
    assert acts["Receive"]["processing_observation_share"] == pytest.approx(11 / 12)
    # The unfinished Review start is not represented in seq2 waiting observations.
    assert acts["Review"]["waiting_observation_share"] == pytest.approx(11 / 12)
    assert acts["Review"]["processing_observations"] == {"observed": 11, "eligible": 12}
    assert acts["Review"]["waiting_observations"] == {"observed": 11, "eligible": 12}
    assert acts["Receive"]["processing"]["population_stddev_seconds"] == 0
    assert acts["Close"]["waiting_before"]["median_seconds"] == 0
    assert result["activities"][0]["activity"] == "Review"   # ranked by total waiting
    hot = result["waiting_hotspots"][0]
    assert (hot["from"], hot["to"]) == ("Receive", "Review")
    i = result["instances"]
    assert i["complete_without_start"] == 1 and i["unmatched_starts"] == 1 and i["ignored_lifecycle_events"] == 1
    assert acts["Receive"]["instances_with_processing_time"] == 11   # c10's complete-only instant is excluded
    assert result["flow_efficiency"]["median"] == pytest.approx(3.5 / 5.5)





def test_hotspot_limit_reports_omitted_transitions_without_hiding_denominator():
    # 30 distinct observed handoffs: only the top 25 can appear in the response.
    rows = []
    for i in range(waiting.MAX_TRANSITIONS + 5):
        case = f"case{i}"
        rows.extend([
            (case, f"Source{i:02d}", _at(i, 0), "start"),
            (case, f"Source{i:02d}", _at(i, 1), "complete"),
            (case, f"Target{i:02d}", _at(i, 2), "start"),
            (case, f"Target{i:02d}", _at(i, 3), "complete"),
        ])
    result = waiting.waiting_time(_import(
        ["case", "activity", "time", "lifecycle:transition"], rows))
    assert result["waiting_hotspot_result_limit"] == waiting.MAX_TRANSITIONS
    assert result["waiting_hotspot_total"] == waiting.MAX_TRANSITIONS + 5
    assert len(result["waiting_hotspots"]) == waiting.MAX_TRANSITIONS
    assert result["waiting_hotspots_omitted"] == 5
    assert result["waiting_hotspots"][0]["from"] == "Source00"
    assert result["waiting_hotspots"][0]["to"] == "Target00"


def test_hotspot_paging_adapter_restricts_source_and_bounds_cursor():
    log = _lifecycle_log()
    first = waiting.waiting_hotspot_cases(log, source="Receive", destination="Review", limit=3)
    assert first["available"] is True
    assert first["total_cases"] >= 10
    assert len(first["cases"]) == 3
    assert first["next_after"] == first["cases"][-1]["case_id"]
    second = waiting.waiting_hotspot_cases(log, source="Receive", destination="Review",
                                        after=first["next_after"], limit=3)
    assert len(second["cases"]) == 3
    assert set(row["case_id"] for row in first["cases"]).isdisjoint(
        row["case_id"] for row in second["cases"])
    assert all(row["waiting_seconds"] >= 0 for row in first["cases"] + second["cases"])
    for kwargs in ({"source": "", "destination": "Review"},
                   {"source": "Receive", "destination": "Review", "limit": 0},
                   {"source": "Receive", "destination": "Review", "limit": True},
                   {"source": "Receive", "destination": "Review", "after": ""}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            waiting.waiting_hotspot_cases(log, **kwargs)


def test_only_unfinished_activity_remains_visible_with_zero_coverage():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("x", "Unfinished", _at(0, 0), "start"),
        ("y", "Unfinished", _at(1, 0), "start"),
        ("z", "Finished", _at(2, 0), "start"),
        ("z", "Finished", _at(2, 1), "complete"),
    ])
    result = waiting.waiting_time(log)
    unfinished = _by(result)["Unfinished"]
    assert unfinished["open_starts"] == 2
    assert unfinished["instances"] == 0
    assert unfinished["processing_observation_share"] == 0
    assert unfinished["processing"]["median_seconds"] is None
    assert unfinished["waiting_observation_share"] == 0
    assert unfinished["waiting_observations"] == {"observed": 0, "eligible": 2}
    assert _by(result)["Finished"]["processing_observation_share"] == 1


def test_unfinished_only_activities_have_case_counts_and_respect_response_limit():
    rows = []
    for i in range(waiting.MAX_ACTIVITIES + 15):
        rows.append((f"case{i}", f"Open{i:03d}", _at(0, i / 100), "start"))
    rows += [("finished", "Finished", _at(1, 0), "start"),
             ("finished", "Finished", _at(1, 1), "complete")]
    result = waiting.waiting_time(_import(
        ["case", "activity", "time", "lifecycle:transition"], rows))
    assert len(result["activities"]) == waiting.MAX_ACTIVITIES
    assert result["activity_result_limit"] == waiting.MAX_ACTIVITIES
    assert result["activity_total"] == waiting.MAX_ACTIVITIES + 16
    assert result["activities_omitted"] == 16
    assert result["activities"][0]["activity"] == "Finished"
    unfinished = result["activities"][1:]
    assert len(unfinished) == waiting.MAX_ACTIVITIES - 1
    assert all(a["cases"] == 1 and a["open_starts"] == 1
               and a["processing_observation_share"] == 0 for a in unfinished)
    assert [a["activity"] for a in unfinished] == sorted(a["activity"] for a in unfinished)



def test_observed_activity_population_is_not_limited_to_dashboard_top_n():
    # An unfinished start for an activity omitted by the ranked top-N is
    # still an observed activity, not a second open-only activity.
    rows = []
    for i in range(waiting.MAX_ACTIVITIES + 5):
        name = f"Activity{i:03d}"
        rows.extend([("case", name, _at(i, 0), "start"),
                     ("case", name, _at(i, 1), "complete")])
    rows.append(("unfinished", "Activity104", _at(200, 0), "start"))
    rows.append(("unfinished", "OnlyOpen", _at(201, 0), "start"))
    result = waiting.waiting_time(_import(
        ["case", "activity", "time", "lifecycle:transition"], rows))
    assert result["activity_total"] == waiting.MAX_ACTIVITIES + 6
    assert len(result["activities"]) == waiting.MAX_ACTIVITIES
    assert result["activities_omitted"] == 6
    assert result["instances"]["unmatched_starts"] == 2



def test_cohort_coverage_includes_omitted_activity_rows():
    # Numeric denominators cover the full cohort, not just the top-N display.
    rows = []
    for i in range(waiting.MAX_ACTIVITIES + 5):
        rows.extend([
            (f"c{i}", f"A{i:03}", _at(i, 0), "start"),
            (f"c{i}", f"A{i:03}", _at(i, 1), "complete"),
        ])
    rows.append(("unfinished", "OnlyOpen", _at(201, 0), "start"))
    result = waiting.waiting_time(_import(
        ["case", "activity", "time", "lifecycle:transition"], rows))
    coverage = result["cohort_observation_coverage"]
    assert result["activities_omitted"] == 6
    assert coverage["eligible"] == waiting.MAX_ACTIVITIES + 6
    assert coverage["processing_observed"] == waiting.MAX_ACTIVITIES + 5
    assert coverage["processing_share"] == pytest.approx(
        (waiting.MAX_ACTIVITIES + 5) / (waiting.MAX_ACTIVITIES + 6))
    assert coverage["waiting_observed"] == 0
    assert coverage["waiting_share"] == 0


def test_invalid_lifecycle_pair_does_not_double_count_coverage_eligibility():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("x", "Review", _at(0, 2), "start"),
        ("x", "Review", _at(0, 1), "complete"),
        ("y", "Review", _at(1, 0), "start"),
        ("y", "Review", _at(1, 1), "complete"),
    ])
    result = waiting.waiting_time(log)
    review = _by(result)["Review"]
    assert result["instances"]["end_before_start"] == 1
    assert result["instances"]["unmatched_starts"] == 1
    assert review["open_starts"] == 1
    assert review["processing_observations"] == {"observed": 1, "eligible": 2}
    assert review["waiting_observations"]["eligible"] == 2
    assert review["processing_observation_share"] == 0.5


def test_open_start_activity_count_deduplicates_across_cases():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("a", "Review", _at(0, 0), "start"),
        ("b", "Review", _at(1, 0), "start"),
        ("c", "Approve", _at(2, 0), "start"),
        ("d", "Finished", _at(3, 0), "start"),
        ("d", "Finished", _at(3, 1), "complete"),
    ])
    result = waiting.waiting_time(log)
    assert result["instances"]["unmatched_starts"] == 3
    # Count distinct unfinished case/activity groups, matching the established API metric.
    assert result["instances"]["activities_with_open_starts"] == 3
    assert _by(result)["Review"]["open_starts"] == 2
    assert _by(result)["Review"]["cases"] == 2


def test_overlapping_work_is_not_waiting_and_counts_once_as_busy():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("x", "A", _at(0, 0), "start"), ("x", "B", _at(0, 1), "start"),
        ("x", "A", _at(0, 2), "complete"), ("x", "B", _at(0, 3), "complete")])
    result = waiting.waiting_time(log)
    assert _by(result)["B"]["waiting_before"]["median_seconds"] == 0
    assert result["flow_efficiency"]["median"] == 1
    assert result["flow_efficiency"]["busy_seconds"] == 3 * 3600


def test_completion_before_start_does_not_close_start():
    log = _import(["case", "activity", "time", "lifecycle:transition"], [
        ("a", "A", _at(0, 0), "complete"), ("a", "A", _at(0, 1), "start"),
        ("a", "A", _at(0, 2), "start"),
        ("b", "B", _at(0, 0), "start"), ("b", "B", _at(0, 1), "complete")])
    instances = waiting.waiting_time(log)["instances"]
    assert instances["end_before_start"] == 1
    assert instances["unmatched_starts"] == 2
    assert instances["activities_with_open_starts"] == 1


def test_end_time_attribute_with_offsets_and_bad_values():
    log = _import(["case", "activity", "time", "finished"], [
        ("a", "Receive", _at(0, 0), "2024-01-01T02:00:00+01:00"),   # 1 h after start in UTC
        ("a", "Review", _at(0, 4), _at(0, 6)),
        ("b", "Receive", _at(1, 0), "2024-01-02 01:00:00"),         # no offset: not trusted
        ("b", "Review", _at(1, 4), _at(1, 3))])                       # ends before it starts
    auto = waiting.waiting_time(log)
    assert auto["available"] is False and "finished" in auto["attribute_names"]
    result = waiting.waiting_time(log, end_attribute="finished")
    assert result["mode_used"] == "end_attribute"
    acts = _by(result)
    assert acts["Receive"]["processing"]["median_seconds"] == 3600
    # a waits 3 h after Receive ends; b's Receive has no trusted end, so it is an instant and b waits 4 h.
    assert acts["Review"]["waiting_before"]["median_seconds"] == 3.5 * 3600
    assert acts["Review"]["waiting_before"]["p10_seconds"] == pytest.approx(3.1 * 3600)
    assert acts["Review"]["waiting_before"]["population_stddev_seconds"] == pytest.approx(1800)
    assert acts["Review"]["waiting_before"]["instances"] == 2
    assert result["instances"]["without_lifecycle_or_end"] == 1 and result["instances"]["end_before_start"] == 1


def test_end_attribute_invalid_end_remains_in_coverage_denominator():
    # Unlike a lifecycle invalid pair, an invalid end-attribute timestamp
    # is not counted again as an unmatched start.
    log = _import(["case", "activity", "time", "finished"], [
        ("a", "Review", _at(0, 0), _at(0, 1)),
        ("b", "Review", _at(1, 2), _at(1, 1)),
        ("c", "Review", _at(2, 0), "no trusted offset"),
    ])
    result = waiting.waiting_time(log, end_attribute="finished")
    review = _by(result)["Review"]
    assert result["instances"]["end_before_start"] == 1
    assert review["processing_observations"] == {"observed": 1, "eligible": 3}
    assert review["processing_observation_share"] == pytest.approx(1 / 3)
    assert review["waiting_observations"]["eligible"] == 3


def test_settings_validation_and_cohorts():
    log = _lifecycle_log()
    for bad in ({"mode": "fast"}, {"mode": "end_attribute"}, {"end_attribute": 'x"y'}, {"lifecycle_attribute": ""}):
        with pytest.raises(workspace.EventLogWorkspaceError):
            waiting.waiting_time(log, **bad)
    only = waiting.waiting_time(log, cohort={"activity": "Close"})
    assert set(_by(only)) == {"Receive", "Review", "Close"} and _by(only)["Close"]["cases"] == 10


def test_real_http_route_requires_a_dashboard_session(tmp_path):
    code = r'''
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
doc = (b"case,activity,time,lifecycle:transition\n1,A,2024-01-01T00:00:00Z,start\n1,A,2024-01-01T01:00:00Z,complete\n"
       b"1,B,2024-01-01T03:00:00Z,start\n1,B,2024-01-01T04:00:00Z,complete\n")
with TestClient(app) as client:
    headers = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    made = client.post("/v1/event-logs/import?format=csv&name=x&case_column=case&activity_column=activity"
                       "&timestamp_column=time&attribute_column=lifecycle:transition", content=doc, headers={**headers, "x-owg-local-storage-confirmed": "true"})
    path = "/v1/event-logs/" + made.json()["log_id"] + "/waiting-time"
    assert client.get(path, headers={"Authorization": "Bearer " + local_auth.ensure_api_token()}).status_code in (401, 403)
    result = client.get(path, headers=headers)
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store", result.text
    assert result.json()["waiting_hotspots"][0]["median_waiting_seconds"] == 7200
    assert result.json()["activities"][0]["waiting_before"]["population_stddev_seconds"] == 0
    assert client.get(path + "?mode=nope", headers=headers).status_code == 422
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"


@pytest.mark.skipif(not os.environ.get("OWG_SCALE_ACCEPTANCE"), reason="scale acceptance runs on demand")
def test_one_million_lifecycle_events_within_budget():
    import time
    rows = ["case,activity,time,lifecycle:transition"]
    for c in range(100_000):
        for s in range(5):
            begin = START + timedelta(minutes=c * 3 + s * 10)
            rows.append(f"c{c},S{s},{begin.isoformat()},start")
            rows.append(f"c{c},S{s},{(begin + timedelta(minutes=4)).isoformat()},complete")
    log = workspace.import_log(BytesIO(("\n".join(rows) + "\n").encode()), name="big", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "attribute_columns": ["lifecycle:transition"]})["log_id"]
    started = time.perf_counter()
    result = waiting.waiting_time(log)
    assert time.perf_counter() - started < 30
    assert _by(result)["S1"]["waiting_before"]["median_seconds"] == 360


def test_csv_attribute_columns_are_kept_and_bounded():
    header = ["case", "activity", "time", "region", "note"]
    rows = [("a", "Receive", _at(0, 0), "north", "x" * 600), ("b", "Receive", _at(1, 0), "south", "")]
    log = _import(header, rows)
    conn = workspace._connect()
    try:
        stored = [r[0] for r in conn.execute(
            "SELECT attributes FROM events WHERE log_id = ? ORDER BY event_id", [log]).fetchall()]
    finally:
        conn.close()
    import json
    first = json.loads(stored[0])
    assert first["region"] == "north" and len(first["note"]) == 500 and "case" not in first
    text = "case,activity,time\nc,A," + _at(0, 0) + "\n"
    for bad in (["missing"], [f"c{i}" for i in range(33)]):
        with pytest.raises(workspace.EventLogWorkspaceError):
            workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
                "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
                "attribute_columns": bad})
    plain = workspace.import_log(BytesIO(text.encode()), name="w", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time"})
    assert plain["events"] == 1
