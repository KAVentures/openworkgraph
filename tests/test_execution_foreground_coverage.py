from server.execution_foreground_coverage import (
    build_foreground_coverage,
    execution_foreground_coverage,
)


def run(actor="human", end="2026-10-01T10:01:00Z"):
    return {"execution_id": "execution:aaaaaaaaaaaaaaaa", "actor_kind": actor,
            "started_at": "2026-10-01T10:00:00Z", "ended_at": end}


def span(at, seconds, surface="surface:gmail", kind="focus_span"):
    return {"event_type": kind, "observed_at": at, "duration_seconds": seconds,
            "surface": surface}


def token(event):
    return event["surface"]


def test_union_clipping_overlap_and_unknown_remainder():
    events = [
        span("2026-10-01T09:59:55Z", 15),
        span("2026-10-01T10:00:05Z", 15),
        span("2026-10-01T10:00:15Z", 15, "surface:github"),
        span("2026-10-01T10:00:55Z", 20, "surface:slack"),
        span("2026-10-01T10:00:10Z", 100, kind="browser_click"),
        span("2026-10-01T10:00:10Z", float("nan")),
        span("invalid", 12),
    ]
    row = execution_foreground_coverage(run(), events, token)
    assert row["window_seconds"] == 60
    assert row["foreground_union_seconds"] == 35
    assert row["overlapping_distinct_surface_seconds"] == 5
    assert row["uncovered_window_seconds"] == 25
    assert row["foreground_span_count"] == 4
    assert row["invalid_span_count"] == 2
    assert row["observed_event_count"] == 7
    assert {r["surface"]: r["exclusive_foreground_seconds"]
            for r in row["surface_rows"]} == {
        "surface:gmail": 15, "surface:github": 10, "surface:slack": 5}
    assert not row["membership_verified"]
    assert not row["coverage_complete"]


def test_same_surface_overlaps_count_once():
    events = [span("2026-10-01T10:00:00Z", 30),
              span("2026-10-01T10:00:00Z", 15),
              span("2026-10-01T10:00:15Z", 30)]
    row = execution_foreground_coverage(run(), events, token)
    assert row["foreground_union_seconds"] == 45
    assert row["overlapping_distinct_surface_seconds"] == 0
    assert row["surface_rows"] == [{"surface": "surface:gmail",
                                    "exclusive_foreground_seconds": 45}]


def test_absent_and_nonhuman_coverage_are_not_zero():
    for actor, status in [("human", "no_retained_foreground_spans"),
                          ("agent", "not_applicable"), ("unknown", "not_applicable")]:
        row = execution_foreground_coverage(run(actor), [], token)
        assert row["status"] == status
        assert row["foreground_union_seconds"] is None
        assert row["uncovered_window_seconds"] is None
    row = execution_foreground_coverage(run(end="2026-10-01T10:00:00Z"), [], token)
    assert row["status"] == "unavailable"
    assert row["window_seconds"] is None


def test_surface_bound_preserves_other_seconds():
    events = [span(f"2026-10-01T10:00:{i:02d}Z", 1, f"surface:{i}")
              for i in range(40)]
    report = build_foreground_coverage([run()], {"execution:aaaaaaaaaaaaaaaa": events}, token)
    row = report["runs"][0]
    assert row["foreground_union_seconds"] == 40
    assert len(row["surface_rows"]) == 30
    assert row["surface_rows_omitted"] == 10
    assert row["exclusive_other_surface_seconds"] == 10
    assert report["uncovered_time_is_unknown"]
    assert report["sums_across_runs_are_not_unique_time"]
    assert report["active_labor_or_waiting_measured"] is False


def test_offsets_and_touching_intervals():
    events = [span("2026-10-01T12:00:00+02:00", 10),
              span("2026-10-01T10:00:10Z", 10, "surface:github")]
    row = execution_foreground_coverage(run(), events, token)
    assert row["foreground_union_seconds"] == 20
    assert row["overlapping_distinct_surface_seconds"] == 0
