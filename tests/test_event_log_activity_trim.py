"""Activity (endpoints) trim for saved copies and matching-case exports, checked against brute force."""
from __future__ import annotations

import csv
import io
import json
import random
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest

from server import event_log_derive as derive
from server import event_log_workspace as workspace
from server.event_log_cohort_export import export_matching_cases
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}
T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _import(traces: dict[str, list[str]], name="t"):
    rows = ["case,activity,time"]
    for case, acts in traces.items():
        rows += [f"{case},{a},{(T0 + timedelta(hours=i)).isoformat().replace('+00:00', 'Z')}" for i, a in enumerate(acts)]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name=name, source_format="csv", mapping=MAPPING)["log_id"]


def _exported(log, **trim):
    out = io.StringIO()
    export_matching_cases(log, {"cohort": {}, **trim}, out)
    result: dict[str, list[str]] = {}
    for row in csv.DictReader(io.StringIO(out.getvalue())):
        result.setdefault(row["case_id"], []).append(row["activity"])
    return result


def _expected(traces, start, end, mode):
    result = {}
    for case, acts in traces.items():
        s = acts.index(start) if start is not None and start in acts else (0 if start is None else None)
        if s is None:
            continue
        if end is None:
            e = len(acts) - 1
        else:
            step = 1 if mode == "first" and start == end else 0
            hits = [i for i, a in enumerate(acts) if a == end and i >= s + step]
            if not hits:
                continue
            e = max(hits) if mode == "longest" else min(hits)
        result[case] = acts[s:e + 1]
    return result


def test_longest_and_first_segments():
    traces = {"c1": ["X", "A", "B", "A", "B", "Y"], "c2": ["A", "Y"], "c3": ["B", "A"], "c4": ["A", "B"]}
    log = _import(traces)
    assert _exported(log, trim_from_activity="A", trim_to_activity="B") == {
        "c1": ["A", "B", "A", "B"], "c4": ["A", "B"]}
    assert _exported(log, trim_from_activity="A", trim_to_activity="B", trim_mode="first") == {
        "c1": ["A", "B"], "c4": ["A", "B"]}
    assert _exported(log, trim_from_activity="A") == {"c1": ["A", "B", "A", "B", "Y"], "c2": ["A", "Y"],
                                                      "c3": ["A"], "c4": ["A", "B"]}
    assert _exported(log, trim_to_activity="A", trim_mode="first") == {
        "c1": ["X", "A"], "c2": ["A"], "c3": ["B", "A"], "c4": ["A"]}
    assert _exported(log, trim_from_activity="A", trim_to_activity="A", trim_mode="first") == {"c1": ["A", "B", "A"]}
    with pytest.raises(EventLogWorkspaceError, match="between the trim activities"):
        _exported(log, trim_from_activity="Y", trim_to_activity="X")


def test_matches_brute_force_on_random_logs():
    rng = random.Random(7)
    for round_ in range(25):
        traces = {f"c{i}": [rng.choice("ABCD") for _ in range(rng.randint(1, 9))] for i in range(rng.randint(3, 12))}
        log = _import(traces, name=f"r{round_}")
        for _ in range(6):
            start, end = rng.choice(["A", "B", None]), rng.choice(["A", "C", None])
            if start is None and end is None:
                continue
            mode = rng.choice(["longest", "first"])
            spec = {"trim_mode": mode, **({"trim_from_activity": start} if start else {}),
                    **({"trim_to_activity": end} if end else {})}
            expected = _expected(traces, start, end, mode)
            if not expected:
                with pytest.raises(EventLogWorkspaceError):
                    _exported(log, **spec)
                continue
            assert _exported(log, **spec) == expected, (traces, spec)
            preview = derive.preview(log, {"cohort": {}, **spec})
            kept = sum(len(v) for v in expected.values())
            assert (preview["events"], preview["kept_cases"]) == (kept, len(expected))
            assert preview["trimmed_by_activity"] == sum(len(v) for v in traces.values()) - kept
        workspace.delete_log(log)


def test_trim_combines_with_a_case_filter_and_time_window():
    traces = {"c1": ["A", "B", "C", "B"], "c2": ["A", "C", "B"], "c3": ["A", "B"]}
    log = _import(traces)
    out = io.StringIO()
    export_matching_cases(log, {"cohort": {"activity": "C"}, "trim_from_activity": "A", "trim_to_activity": "B",
                                "events_before": (T0 + timedelta(hours=3)).isoformat()}, out)
    rows = [(r["case_id"], r["activity"]) for r in csv.DictReader(io.StringIO(out.getvalue()))]
    # The window drops c1's last B, so its segment ends at the first B.
    assert rows == [("c1", "A"), ("c1", "B"), ("c2", "A"), ("c2", "C"), ("c2", "B")]


def test_saved_copy_reports_the_trim_and_keeps_the_source():
    traces = {"c1": ["X", "A", "B", "Y"], "c2": ["X", "Y"]}
    log = _import(traces)
    r = derive.derive(log, {"cohort": {}, "name": "Trimmed", "trim_from_activity": "A", "trim_to_activity": "B"})
    assert r["activity_trim"] == {"from_activity": "A", "to_activity": "B", "mode": "longest",
                                  "events_left_out": 4, "cases_with_endpoints": 1}
    assert r["copied"]["events"] == 2 and "time_window" not in r
    assert "between the trim activities" in r["interpretation"]
    new = r["log"]["log_id"]
    assert [e["activity"] for _, events in workspace.case_events(new) for e in events] == ["A", "B"]
    assert sum(len(events) for _, events in workspace.case_events(log)) == 6


def test_object_centric_flat_export_keeps_each_case_segment():
    rel = lambda *ids: [{"objectId": i, "qualifier": ""} for i in ids]
    ocel = {"objectTypes": [{"name": "item", "attributes": []}],
            "eventTypes": [{"name": n, "attributes": []} for n in "ASE"],
            "objects": [{"id": "i1", "type": "item"}, {"id": "i2", "type": "item"}],
            "events": [{"id": "e1", "type": "A", "time": "2024-01-01T00:00:00Z", "relationships": rel("i1")},
                       {"id": "e2", "type": "S", "time": "2024-01-02T00:00:00Z", "relationships": rel("i1", "i2")},
                       {"id": "e3", "type": "E", "time": "2024-01-03T00:00:00Z", "relationships": rel("i1")},
                       {"id": "e4", "type": "A", "time": "2024-01-04T00:00:00Z", "relationships": rel("i2")},
                       {"id": "e5", "type": "E", "time": "2024-01-05T00:00:00Z", "relationships": rel("i2")}]}
    log = workspace.import_log(BytesIO(json.dumps(ocel).encode()), name="o", source_format="ocel")["log_id"]
    # The shared event S is inside i1's segment but before i2's, so only i1 keeps it.
    assert _exported(log, trim_from_activity="A", trim_to_activity="E") == {"i1": ["A", "S", "E"], "i2": ["A", "E"]}


def test_settings_are_validated():
    log = _import({"c1": ["A", "B"]})
    with pytest.raises(EventLogWorkspaceError, match="Invalid matching-case export settings"):
        _exported(log, trim_mode="shortest")
    with pytest.raises(EventLogWorkspaceError, match="trim_from_activity"):
        derive.preview(log, {"cohort": {}, "trim_from_activity": ""})
    with pytest.raises(EventLogWorkspaceError, match="activity trim"):
        derive.preview(log, {"cohort": {}})
