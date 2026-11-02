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


def test_explicit_iana_local_weekday_hour_tracks_spring_and_fall_dst():
    # Stockholm spring jump skips 02:xx; autumn 02:xx repeats. These are
    # observed wall-clock bins, not disjoint UTC hours or labor-time estimates.
    log = _import([
        ("spring_before", "Seen", "2024-03-31T00:30:00Z"),
        ("spring_after", "Seen", "2024-03-31T01:30:00Z"),
        ("fall_first", "Seen", "2024-10-27T00:30:00Z"),
        ("fall_second", "Seen", "2024-10-27T01:30:00Z"),
    ])
    result = query.run(log, {
        "level": "cases", "dimensions": ["start_weekday_local", "start_hour_local"],
        "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}],
        "order": "dimensions",
    })
    assert result["rows"] == [
        ["7 Sunday", "01", 1],
        ["7 Sunday", "02", 2],
        ["7 Sunday", "03", 1],
    ]
    assert result["spec"]["time_zone"] == "Europe/Stockholm"
    assert result["matching"]["cases"] == 4
    assert result["rows_truncated"] is False


def test_local_start_and_end_may_cross_weekday_boundary_and_can_be_saved():
    log = _import([
        ("overnight", "Start", "2024-01-07T23:30:00Z"),
        ("overnight", "End", "2024-01-08T01:30:00Z"),
    ])
    spec = {
        "level": "cases", "dimensions": ["start_weekday_local", "end_hour_local"],
        "time_zone": "Europe/Stockholm",
        "measures": [{"fn": "count"}], "order": "dimensions",
    }
    saved = query.save(log, {"name": "Stockholm overnight", "spec": spec})
    reloaded = query.list_saved(log)["analyses"][0]
    assert reloaded["analysis_id"] == saved["analysis_id"]
    assert reloaded["spec"]["time_zone"] == "Europe/Stockholm"
    assert query.run(log, reloaded["spec"])["rows"] == [["1 Monday", "02", 1]]
    assert _run(log, ["start_weekday_utc", "start_hour_utc"])["rows"] == [
        ["7 Sunday", "23", 1],
    ]


@pytest.mark.parametrize("zone", [
    None, "Not/A_Real_Time_Zone", "../etc/passwd", "Europe/Stockholm'; DROP TABLE ce; --",
])
def test_local_time_requires_a_valid_bound_iana_zone(zone):
    spec = {"level": "cases", "dimensions": ["start_hour_local"],
            "measures": [{"fn": "count"}]}
    if zone is not None:
        spec["time_zone"] = zone
    with pytest.raises(workspace.EventLogWorkspaceError, match="time_zone|analysis is invalid"):
        query.parse(spec)


def test_zone_cannot_silently_change_utc_or_event_level_analyses():
    for spec in [
        {"level": "cases", "dimensions": ["start_hour_utc"],
         "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}]},
        {"level": "events", "dimensions": ["hour"],
         "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}]},
        {"level": "events", "dimensions": ["start_hour_local"],
         "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}]},
    ]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            query.parse(spec)


def test_local_time_zone_parameter_order_with_case_attribute_dimension():
    # Both the IANA zone and attribute name are bound SELECT parameters;
    # neither can be spliced into the SQL compiler as an identifier.
    log = _cases()
    result = query.run(log, {
        "level": "cases", "dimensions": ["start_hour_local", "attribute:priority"],
        "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}],
        "order": "dimensions",
    })
    assert result["matching"]["cases"] == 5
    assert sum(row[-1] for row in result["rows"]) == 5


def test_event_level_iana_local_hour_weekday_counts_distinct_cases_and_shares():
    # Spring skips 02:xx; autumn repeats 02:xx. The same local hour
    # contains two distinct UTC hours and two source cases, not three.
    log = _import([
        ("spring_before", "Seen", "2024-03-31T00:30:00Z"),
        ("spring_after", "Seen", "2024-03-31T01:30:00Z"),
        ("fall_first", "Seen", "2024-10-27T00:30:00Z"),
        ("fall_first", "Seen", "2024-10-27T00:45:00Z"),
        ("fall_second", "Seen", "2024-10-27T01:30:00Z"),
        ("fall_second", "Other", "2024-10-27T01:31:00Z"),
    ])
    spec = {
        "level": "events",
        "dimensions": ["weekday_local", "hour_local"],
        "time_zone": "Europe/Stockholm",
        "activity_in": ["Seen"],
        "measures": [{"fn": "count"}, {"fn": "cases"}, {"fn": "share"}],
        "order": "dimensions",
        "limit": 168,
    }
    result = query.run(log, spec)
    assert result["columns"] == [
        "weekday_local", "hour_local", "count", "cases", "share"
    ]
    assert result["rows"] == [
        ["7 Sunday", "01", 1, 1, 0.2],
        ["7 Sunday", "02", 3, 2, 0.6],
        ["7 Sunday", "03", 1, 1, 0.2],
    ]
    assert result["matching"] == {"rows": 5, "cases": 4}
    assert result["rows_truncated"] is False

    saved = query.save(log, {"name": "Local observed events", "spec": spec})
    reloaded = query.list_saved(log)["analyses"][0]
    assert reloaded["analysis_id"] == saved["analysis_id"]
    assert reloaded["spec"]["time_zone"] == "Europe/Stockholm"
    assert query.run(log, reloaded["spec"])["rows"] == result["rows"]


def test_event_local_dimension_and_attribute_bind_parameters_before_filters():
    log = _import([
        ("a", "Seen", "2024-01-01T23:30:00Z"),
        ("b", "Seen", "2024-01-02T00:30:00Z"),
        ("c", "Other", "2024-01-02T00:31:00Z"),
    ])
    result = query.run(log, {
        "level": "events",
        "dimensions": ["attribute:priority", "hour_local"],
        "time_zone": "Europe/Stockholm",
        "activity_in": ["Seen"],
        "measures": [{"fn": "count"}],
        "order": "dimensions",
    })
    assert result["rows"] == [[None, "00", 1], [None, "01", 1]]
    assert result["matching"] == {"rows": 2, "cases": 2}
    # Existing UTC hour remains UTC, with no zone required.
    utc = query.run(log, {
        "level": "events", "dimensions": ["hour"],
        "activity_in": ["Seen"], "measures": [{"fn": "count"}],
        "order": "dimensions",
    })
    assert utc["matching"]["rows"] == 2
    assert sorted(row[-1] for row in utc["rows"]) == [1, 1]


def test_event_local_zone_is_required_and_cannot_change_utc_event_dimensions():
    for spec in [
        {"level": "events", "dimensions": ["hour_local"],
         "measures": [{"fn": "count"}]},
        {"level": "events", "dimensions": ["weekday_local"],
         "time_zone": "Not/A_Real_Time_Zone", "measures": [{"fn": "count"}]},
        {"level": "events", "dimensions": ["hour"],
         "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}]},
        {"level": "cases", "dimensions": ["hour_local"],
         "time_zone": "Europe/Stockholm", "measures": [{"fn": "count"}]},
    ]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            query.parse(spec)


def test_composite_utc_event_bucket_can_be_grouped_by_activity():
    # Two dimensions suffice to compare activities across all 168 hourly bins.
    log = _import([
        ("a", "Approve", "2024-01-01T00:30:00Z"),
        ("a", "Approve", "2024-01-01T00:45:00Z"),
        ("b", "Approve", "2024-01-01T00:50:00Z"),
        ("c", "Review", "2024-01-01T00:55:00Z"),
        ("c", "Review", "2024-01-02T01:30:00Z"),
    ])
    spec = {
        "level": "events", "dimensions": ["activity", "weekday_hour_utc"],
        "measures": [{"fn": "count"}, {"fn": "cases"}, {"fn": "share"}],
        "order": "dimensions", "limit": 168,
    }
    result = query.run(log, spec)
    assert result["rows"] == [
        ["Approve", "1 Monday 00", 3, 2, 0.6],
        ["Review", "1 Monday 00", 1, 1, 0.2],
        ["Review", "2 Tuesday 01", 1, 1, 0.2],
    ]
    assert result["matching"] == {"rows": 5, "cases": 3}
    assert result["rows_truncated"] is False


def test_composite_local_event_bucket_combines_dst_fold_and_preserves_activity():
    log = _import([
        ("a", "Seen", "2024-03-31T00:30:00Z"),
        ("b", "Seen", "2024-03-31T01:30:00Z"),
        ("c", "Seen", "2024-10-27T00:30:00Z"),
        ("d", "Seen", "2024-10-27T01:30:00Z"),
        ("e", "Other", "2024-10-27T01:45:00Z"),
    ])
    spec = {
        "level": "events", "dimensions": ["weekday_hour_local", "activity"],
        "time_zone": "Europe/Stockholm",
        "measures": [{"fn": "count"}, {"fn": "cases"}],
        "order": "dimensions", "limit": 168,
    }
    result = query.run(log, spec)
    assert result["rows"] == [
        ["7 Sunday 01", "Seen", 1, 1],
        ["7 Sunday 02", "Other", 1, 1],
        ["7 Sunday 02", "Seen", 2, 2],
        ["7 Sunday 03", "Seen", 1, 1],
    ]
    saved = query.save(log, {"name": "Observed local activity by hour", "spec": spec})
    reloaded = query.list_saved(log)["analyses"][0]
    assert reloaded["analysis_id"] == saved["analysis_id"]
    assert query.run(log, reloaded["spec"])["rows"] == result["rows"]


def test_composite_case_intake_and_completion_local_bins_with_second_dimension():
    log = _import([
        ("a", "Intake", "2024-01-07T23:30:00Z"),
        ("a", "Done", "2024-01-08T01:30:00Z"),
        ("b", "Review", "2024-01-08T00:30:00Z"),
        ("b", "Done", "2024-01-08T01:40:00Z"),
    ])
    result = query.run(log, {
        "level": "cases",
        "dimensions": ["start_weekday_hour_local", "first_activity"],
        "time_zone": "Europe/Stockholm",
        "measures": [{"fn": "count"}], "order": "dimensions",
    })
    assert result["rows"] == [
        ["1 Monday 00", "Intake", 1],
        ["1 Monday 01", "Review", 1],
    ]
    completion = query.run(log, {
        "level": "cases",
        "dimensions": ["end_weekday_hour_local", "last_activity"],
        "time_zone": "Europe/Stockholm",
        "measures": [{"fn": "count"}], "order": "dimensions",
    })
    assert completion["rows"] == [["1 Monday 02", "Done", 2]]
    utc = query.run(log, {
        "level": "cases",
        "dimensions": ["start_weekday_hour_utc", "first_activity"],
        "measures": [{"fn": "count"}], "order": "dimensions",
    })
    assert utc["rows"] == [
        ["1 Monday 00", "Review", 1],
        ["7 Sunday 23", "Intake", 1],
    ]


def test_composite_time_buckets_reject_implicit_zone_and_cross_level_scope():
    invalid = [
        {"level": "events", "dimensions": ["weekday_hour_local"]},
        {"level": "cases", "dimensions": ["start_weekday_hour_local"]},
        {"level": "events", "dimensions": ["weekday_hour_utc"], "time_zone": "Europe/Stockholm"},
        {"level": "cases", "dimensions": ["start_weekday_hour_utc"], "time_zone": "Europe/Stockholm"},
        {"level": "events", "dimensions": ["start_weekday_hour_local"], "time_zone": "Europe/Stockholm"},
        {"level": "cases", "dimensions": ["weekday_hour_local"], "time_zone": "Europe/Stockholm"},
    ]
    for spec in invalid:
        with pytest.raises(workspace.EventLogWorkspaceError):
            query.parse({**spec, "measures": [{"fn": "count"}]})


def test_time_of_week_case_start_and_end_windows_are_distinct_and_filtered():
    log = _cases()
    start = {
        "level": "cases", "dimensions": ["first_activity"],
        "measures": [{"fn": "count"}, {"fn": "median", "of": "duration_seconds"}],
        "time_of_week": {"anchor": "case_start", "weekdays": [1], "hours": [22]},
        "order": "dimensions",
    }
    result = query.run(log, start)
    assert result["rows"] == [["Start", 2, 4500.0]]
    assert result["matching"] == {"rows": 2, "cases": 2}
    # The same cases do not all finish on Monday in hour 23 UTC.
    completion = query.run(log, {
        **start, "time_of_week": {"anchor": "case_end", "weekdays": [1], "hours": [23]},
    })
    assert completion["rows"] == [["Start", 1, 1800.0]]
    assert completion["matching"] == {"rows": 1, "cases": 1}
    # Existing case filters are composed with the time window.
    narrowed = query.run(log, {
        **start, "where": {"duration_over_seconds": 2000},
    })
    assert narrowed["rows"] == [["Start", 1, 7200.0]]
    assert narrowed["matching"]["cases"] == 1


def test_time_of_week_event_window_combines_dst_fold_and_activity_filter():
    log = _import([
        ("a", "Seen", "2024-03-31T00:30:00Z"),  # Sunday 01 Stockholm
        ("b", "Seen", "2024-03-31T01:30:00Z"),  # Sunday 03 Stockholm
        ("c", "Seen", "2024-10-27T00:30:00Z"),  # Sunday 02 CEST
        ("d", "Seen", "2024-10-27T01:30:00Z"),  # Sunday 02 CET
        ("e", "Other", "2024-10-27T01:45:00Z"),
    ])
    spec = {
        "level": "events", "dimensions": ["activity"],
        "time_zone": "Europe/Stockholm",
        "time_of_week": {"anchor": "event", "clock": "local",
                         "weekdays": [7], "hours": [2]},
        "activity_in": ["Seen"],
        "measures": [{"fn": "count"}, {"fn": "cases"}, {"fn": "share"}],
        "order": "dimensions",
    }
    result = query.run(log, spec)
    assert result["rows"] == [["Seen", 2, 2, 1.0]]
    assert result["matching"] == {"rows": 2, "cases": 2}
    saved = query.save(log, {"name": "Observed Sunday 02 activity", "spec": spec})
    listed = query.list_saved(log)["analyses"][0]
    assert listed["analysis_id"] == saved["analysis_id"]
    assert listed["spec"]["time_of_week"]["hours"] == [2]
    assert query.run(log, listed["spec"])["rows"] == result["rows"]
    utc = query.run(log, {
        **spec, "time_zone": None,
        "time_of_week": {"anchor": "event", "weekdays": [7], "hours": [0]},
    })
    assert utc["rows"] == [["Seen", 2, 2, 1.0]]
    assert utc["matching"]["cases"] == 2


def test_time_of_week_case_local_window_can_combine_with_utc_dimension():
    log = _import([
        ("a", "Intake", "2024-01-07T23:30:00Z"),
        ("a", "Done", "2024-01-08T01:30:00Z"),
        ("b", "Review", "2024-01-08T00:30:00Z"),
        ("b", "Done", "2024-01-08T01:40:00Z"),
    ])
    spec = {
        "level": "cases", "dimensions": ["start_weekday_hour_utc"],
        "time_zone": "Europe/Stockholm",
        "time_of_week": {"anchor": "case_start", "clock": "local",
                         "weekdays": [1], "hours": [0]},
        "measures": [{"fn": "count"}], "order": "dimensions",
    }
    result = query.run(log, spec)
    assert result["rows"] == [["7 Sunday 23", 1]]
    assert result["matching"]["cases"] == 1
    completion = query.run(log, {
        **spec, "dimensions": ["first_activity"],
        "time_of_week": {"anchor": "case_end", "clock": "local",
                         "weekdays": [1], "hours": [2]},
    })
    assert completion["rows"] == [["Intake", 1], ["Review", 1]]
    assert completion["matching"]["cases"] == 2


@pytest.mark.parametrize("level,window,zone", [
    ("events", {"anchor": "case_start"}, None),
    ("cases", {"anchor": "event"}, None),
    ("events", {"anchor": "event", "clock": "local"}, None),
    ("events", {"anchor": "event", "clock": "local"}, "Invalid/Zone"),
    ("events", {"anchor": "event", "clock": "utc"}, "Europe/Stockholm"),
    ("cases", {"anchor": "case_end", "weekdays": []}, None),
    ("cases", {"anchor": "case_end", "weekdays": [1, 1]}, None),
    ("cases", {"anchor": "case_start", "weekdays": [0]}, None),
    ("cases", {"anchor": "case_start", "weekdays": [True]}, None),
    ("events", {"anchor": "event", "hours": [24]}, None),
    ("events", {"anchor": "event", "hours": [-1]}, None),
    ("events", {"anchor": "event", "hours": [1, 1]}, None),
    ("events", {"anchor": "event", "hours": ["02"]}, None),
    ("events", {"anchor": "event", "clock": "wrong"}, None),
    ("events", {"anchor": "event", "extra_sql": "DROP TABLE ce"}, None),
])
def test_time_of_week_rejects_invalid_or_ambiguous_windows(level, window, zone):
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.parse({
            "level": level, "dimensions": [], "measures": [{"fn": "count"}],
            "time_of_week": window, **({"time_zone": zone} if zone else {}),
        })


def test_exact_weekday_hour_cells_avoid_cross_product_for_observed_events():
    log = _import([
        ("s22", "Sunday22", "2024-01-07T22:15:00Z"),
        ("s01", "Sunday01", "2024-01-07T01:15:00Z"),
        ("m01", "Monday01", "2024-01-08T01:15:00Z"),
        ("m05", "Monday05", "2024-01-08T05:15:00Z"),
        ("m22", "Monday22", "2024-01-08T22:15:00Z"),
        ("t01", "Tuesday01", "2024-01-09T01:15:00Z"),
    ])
    slots = [{"weekday": 7, "hour": 22}, {"weekday": 1, "hour": 1},
             {"weekday": 1, "hour": 5}]
    spec = {
        "level": "events", "dimensions": ["activity"],
        "time_of_week": {"anchor": "event", "slots": slots},
        "measures": [{"fn": "count"}, {"fn": "cases"}],
        "order": "dimensions",
    }
    result = query.run(log, spec)
    assert result["rows"] == [
        ["Monday01", 1, 1], ["Monday05", 1, 1], ["Sunday22", 1, 1],
    ]
    assert result["matching"] == {"rows": 3, "cases": 3}
    assert result["spec"]["time_of_week"]["slots"] == slots

    # The older Cartesian-product selector is intentionally unchanged: it
    # includes Sunday 01 and Monday 22, which the exact-cell selector excludes.
    legacy = query.run(log, {
        **spec, "time_of_week": {"anchor": "event",
                                "weekdays": [7, 1], "hours": [22, 1, 5]},
    })
    assert legacy["matching"] == {"rows": 5, "cases": 5}


def test_exact_cells_select_case_intake_or_completion_and_survive_save():
    log = _import([
        ("a", "Intake", "2024-01-07T22:10:00Z"),
        ("a", "Done", "2024-01-08T01:15:00Z"),
        ("b", "Intake", "2024-01-08T22:10:00Z"),
        ("b", "Done", "2024-01-09T01:15:00Z"),
    ])
    spec = {
        "level": "cases", "dimensions": ["duration_band"],
        "time_of_week": {"anchor": "case_start",
                         "slots": [{"weekday": 7, "hour": 22}]},
        "measures": [{"fn": "count"}], "order": "dimensions",
    }
    start = query.run(log, spec)
    assert start["rows"] == [["1: 1 to under 24 hours", 1]]
    assert start["matching"]["cases"] == 1
    completion = query.run(log, {
        **spec, "time_of_week": {"anchor": "case_end",
                                "slots": [{"weekday": 1, "hour": 1}]},
    })
    assert completion["matching"]["cases"] == 1
    saved = query.save(log, {"name": "Observed Sunday overnight intake", "spec": spec})
    listed = query.list_saved(log)["analyses"][0]
    assert listed["analysis_id"] == saved["analysis_id"]
    assert query.run(log, listed["spec"])["rows"] == start["rows"]


def test_exact_cells_in_local_clock_include_both_dst_fold_instants_not_gap():
    log = _import([
        ("fall_a", "Seen", "2024-10-27T00:30:00Z"),
        ("fall_b", "Seen", "2024-10-27T01:30:00Z"),
        ("spring_a", "Seen", "2024-03-31T00:30:00Z"),
        ("spring_b", "Seen", "2024-03-31T01:30:00Z"),
        ("other", "Seen", "2024-10-28T01:30:00Z"),
        ("excluded", "Other", "2024-10-27T01:45:00Z"),
    ])
    spec = {
        "level": "events", "dimensions": ["activity"],
        "activity_in": ["Seen"], "time_zone": "Europe/Stockholm",
        "time_of_week": {"anchor": "event", "clock": "local",
                         "slots": [{"weekday": 7, "hour": 2}]},
        "measures": [{"fn": "count"}, {"fn": "cases"}],
        "order": "dimensions",
    }
    result = query.run(log, spec)
    assert result["rows"] == [["Seen", 2, 2]]
    assert result["matching"] == {"rows": 2, "cases": 2}
    assert query.run(log, {
        **spec, "time_of_week": {"anchor": "event", "clock": "local",
                                "slots": [{"weekday": 7, "hour": 1}]},
    })["matching"]["rows"] == 1  # Spring's 01:30 CET; no spring 02:xx


def test_exact_cells_compile_to_fixed_sql_and_bound_values():
    utc = query.parse({
        "level": "events", "dimensions": [], "measures": [{"fn": "count"}],
        "time_of_week": {"anchor": "event", "slots": [{"weekday": 7, "hour": 22}]},
    })
    sql, params = query._time_of_week_clause(utc)
    assert sql == "list_contains(?, strftime(ts, '%u%H'))"
    assert params == [["722"]]
    local = query.parse({
        "level": "cases", "dimensions": [], "measures": [{"fn": "count"}],
        "time_zone": "Europe/Stockholm",
        "time_of_week": {"anchor": "case_end", "clock": "local",
                         "slots": [{"weekday": 1, "hour": 0},
                                   {"weekday": 7, "hour": 23}]},
    })
    sql, params = query._time_of_week_clause(local)
    assert sql == "list_contains(?, strftime(timezone(?, timezone('UTC', ended)), '%u%H'))"
    assert params == [["100", "723"], "Europe/Stockholm"]


@pytest.mark.parametrize("window", [
    {"anchor": "event", "slots": []},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 22}] * 2},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 22}], "weekdays": [7]},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 22}], "hours": [22]},
    {"anchor": "event", "slots": [{"weekday": 0, "hour": 22}]},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 24}]},
    {"anchor": "event", "slots": [{"weekday": True, "hour": 22}]},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": "22"}]},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 22, "sql": "x"}]},
    {"anchor": "event", "slots": [{"weekday": 7, "hour": 22}] * 169},
])
def test_exact_cells_reject_mixed_duplicate_malformed_or_unbounded_input(window):
    with pytest.raises(workspace.EventLogWorkspaceError):
        query.parse({
            "level": "events", "dimensions": [], "measures": [{"fn": "count"}],
            "time_of_week": window,
        })
