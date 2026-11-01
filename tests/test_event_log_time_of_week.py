"""R9: bounded UTC case time-of-week and elapsed-duration analytics.

Synthetic imports test local typed REST-compatible analysis behavior; they do
not establish real-provider coverage or the accuracy of upstream timestamps.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_query as query
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _import(rows):
    csv = "case,activity,time,resource\n" + "\n".join(
        f"{case},{activity},{stamp},redacted" for case, activity, stamp in rows
    ) + "\n"
    return workspace.import_log(
        BytesIO(csv.encode("utf-8")), name="time-of-week synthetic",
        source_format="csv",
        mapping={"case_column": "case", "activity_column": "activity",
                 "timestamp_column": "time", "resource_column": "resource"},
    )["log_id"]


def _cases():
    return _import([
        ("a", "Start", "2024-01-01T23:30:00+01:00"),
        ("a", "End", "2024-01-02T01:30:00+01:00"),
        ("b", "Start", "2024-01-01T22:30:00Z"),
        ("b", "End", "2024-01-01T23:00:00Z"),
        ("c", "Start", "2024-01-03T08:00:00Z"),
        ("c", "End", "2024-01-04T09:00:00Z"),
        ("d", "Start", "2024-01-05T10:00:00Z"),
        ("d", "End", "2024-01-13T10:00:00Z"),
        ("e", "Start", "2024-01-07T09:00:00Z"),
        ("e", "End", "2024-01-07T09:00:00Z"),
    ])


def _run(log_id, dimensions, *, where=None):
    return query.run(log_id, {
        "level": "cases", "dimensions": dimensions,
        "measures": [{"fn": "count"}], "order": "dimensions",
        **({"where": where} if where is not None else {}),
    })


def test_case_start_weekday_hour_are_utc_normalized_and_composable():
    log = _cases()
    result = _run(log, ["start_weekday_utc", "start_hour_utc"])
    assert result["columns"] == ["start_weekday_utc", "start_hour_utc", "count"]
    assert result["rows"] == [
        ["1 Monday", "22", 2],
        ["3 Wednesday", "08", 1],
        ["5 Friday", "10", 1],
        ["7 Sunday", "09", 1],
    ]
    assert result["matching"] == {"rows": 5, "cases": 5}
    assert result["rows_truncated"] is False
    assert result["query_basis"] == "typed_spec_compiled_to_parameterized_sql"


def test_case_completion_uses_end_utc_not_start_time_or_local_offset():
    result = _run(_cases(), ["end_weekday_utc", "end_hour_utc"])
    assert result["rows"] == [
        ["1 Monday", "23", 1],
        ["2 Tuesday", "00", 1],
        ["4 Thursday", "09", 1],
        ["6 Saturday", "10", 1],
        ["7 Sunday", "09", 1],
    ]


def test_duration_bands_are_half_open_and_filter_on_whole_cases():
    log = _cases()
    assert _run(log, ["duration_band"])["rows"] == [
        ["0: under 1 hour", 2],
        ["1: 1 to under 24 hours", 1],
        ["2: 1 to under 7 days", 1],
        ["3: 7 days or more", 1],
    ]
    result = _run(log, ["start_weekday_utc", "duration_band"],
                  where={"duration_over_seconds": 3600})
    assert result["rows"] == [
        ["1 Monday", "1: 1 to under 24 hours", 1],
        ["3 Wednesday", "2: 1 to under 7 days", 1],
        ["5 Friday", "3: 7 days or more", 1],
    ]
    assert result["matching"]["cases"] == 3


def test_exact_band_boundaries_and_single_event_case():
    rows = []
    for case, seconds in [("zero", 0), ("hour", 3600),
                          ("day", 86400), ("week", 604800)]:
        from datetime import datetime, timedelta, timezone
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        rows.append((case, "Start", start.isoformat()))
        if seconds:
            rows.append((case, "End", (start + timedelta(seconds=seconds)).isoformat()))
    result = _run(_import(rows), ["duration_band"])
    assert result["rows"] == [
        ["0: under 1 hour", 1],
        ["1: 1 to under 24 hours", 1],
        ["2: 1 to under 7 days", 1],
        ["3: 7 days or more", 1],
    ]


def test_new_dimensions_can_be_saved_and_reused_without_new_authority():
    log = _cases()
    spec = {"level": "cases", "dimensions": ["start_weekday_utc", "start_hour_utc"],
            "measures": [{"fn": "count"}, {"fn": "median", "of": "duration_seconds"}],
            "order": "dimensions", "limit": 20}
    saved = query.save(log, {"name": "UTC intake by day and hour", "spec": spec})
    listed = query.list_saved(log)["analyses"]
    assert len(listed) == 1
    assert listed[0]["analysis_id"] == saved["analysis_id"]
    assert listed[0]["spec"]["dimensions"] == spec["dimensions"]
    result = query.run(log, listed[0]["spec"])
    assert result["columns"] == [
        "start_weekday_utc", "start_hour_utc", "count", "median_duration_seconds"
    ]
    assert result["rows"][0][:3] == ["1 Monday", "22", 2]
    assert result["units"]["median_duration_seconds"] == "seconds"


def test_new_dimensions_do_not_grant_raw_sql_or_event_case_scope_mixing():
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown events dimension"):
        query.parse({"level": "events", "dimensions": ["start_weekday_utc"],
                     "measures": [{"fn": "count"}]})
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown cases dimension"):
        query.parse({"level": "cases", "dimensions": ["start_hour_utc; DROP TABLE events"],
                     "measures": [{"fn": "count"}]})
