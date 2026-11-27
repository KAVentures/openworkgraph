"""Temporal profile: usual times between activity pairs, and the cases that deviate."""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_temporal_profile as tp
from server import event_log_workspace as workspace
from server.event_log_workspace import EventLogWorkspaceError

MAPPING = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time"}


@pytest.fixture(autouse=True)
def _dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()


def _log():
    # 20 orders: Ship a few hours after Order, Invoice two days after Order -- except c0, invoiced weeks later.
    rows = ["case,activity,time"]
    for i in range(20):
        day = 1 + i % 5
        rows += [f"c{i},Order,2024-01-{day:02d}T09:00:00Z", f"c{i},Ship,2024-01-{day:02d}T1{i % 3}:00:00Z",
                 "c0,Invoice,2024-02-20T09:00:00Z" if i == 0 else f"c{i},Invoice,2024-01-{day + 2:02d}T09:{i:02d}:00Z"]
    return workspace.import_log(BytesIO("\n".join(rows).encode()), name="orders", source_format="csv",
                                mapping=MAPPING)["log_id"]


def test_far_apart_deviation_is_found_even_when_adjacent_steps_look_normal():
    result = tp.temporal_profile(_log())
    assert result["cases"] == 20 and result["deviating_cases"] == 1 and result["deviating_case_share"] == 0.05
    top = result["top_cases"][0]
    assert top["case_id"] == "c0" and top["strongest"]["to"] == "Invoice" and top["strongest"]["standard_deviations"] > 3
    by = {(p["from"], p["to"]): p for p in result["pairs"]}
    assert by[("Order", "Invoice")]["deviations"] == 1 and by[("Order", "Invoice")]["slower"] == 1
    assert by[("Order", "Ship")]["deviations"] == 0
    low, high = by[("Order", "Ship")]["allowed_seconds"]
    assert low == 0.0 and high > by[("Order", "Ship")]["mean_seconds"]
    assert result["pairs"][0]["deviations"] >= result["pairs"][-1]["deviations"]


def test_zeta_and_support_change_what_is_checked():
    log = _log()
    loose = tp.temporal_profile(log, zeta=10)
    assert loose["deviating_cases"] == 0
    strict = tp.temporal_profile(log, zeta=0.5)
    assert strict["deviating_cases"] > 1
    assert tp.temporal_profile(log, min_occurrences=50)["profile_pairs"] == 0


def test_case_filter_restricts_the_profile():
    log = _log()
    # Leaves out the four cases that started on 1 January, c0 among them.
    result = tp.temporal_profile(log, cohort={"started_from": "2024-01-02T00:00:00Z"})
    assert result["cases"] == 16 and result["deviating_cases"] == 0 and result["cases_before_filter"] == 20


def test_cutoff_fits_only_earlier_cases_and_checks_later_cases():
    rows = ["case,activity,time"]
    for index in range(6):
        rows += [f"base{index},Order,2024-01-01T09:00:00Z",
                 f"base{index},Invoice,2024-01-01T09:{8 + index:02d}:00Z"]
    rows += [
        "cross,Order,2024-01-01T23:59:00Z", "cross,Invoice,2024-01-02T00:00:00Z",
        "late,Order,2024-01-02T00:00:00Z", "late,Invoice,2024-01-02T01:00:00Z",
        "ordinary,Order,2024-01-02T09:00:00Z", "ordinary,Invoice,2024-01-02T09:10:00Z",
        "unknown,Order,2024-01-02T09:00:00Z", "unknown,Escalate,2024-01-02T09:40:00Z",
    ]
    log = workspace.import_log(BytesIO("\n".join(rows).encode()), name="baseline",
                                source_format="csv", mapping=MAPPING)["log_id"]
    cutoff = "2024-01-02T01:00:00+01:00"
    result = tp.temporal_profile(log, baseline_cutoff=cutoff)
    assert result["cases"] == 3 and result["deviating_cases"] == 1 and result["pairs_checked"] == 2
    assert result["baseline"] == {"cutoff": "2024-01-02T00:00:00Z", "baseline_cases": 6,
                                 "assessment_cases": 3, "crossing_cases": 1,
                                 "assessment_cases_with_profile": 2, "assessment_pair_occurrences": 3,
                                 "assessment_pair_occurrences_without_profile": 1}
    pair, = result["pairs"]
    assert (pair["from"], pair["to"]) == ("Order", "Invoice")
    assert pair["baseline_occurrences"] == 6 and pair["occurrences"] == 2
    assert pair["mean_seconds"] == 630 and pair["deviation_share"] == 0.5
    page = tp.temporal_cases(log, baseline_cutoff=cutoff, source="Order", destination="Invoice",
                             direction="slower")
    assert [row["case_id"] for row in page["cases"]] == ["late"]
    assert page["deviations"] == pair["slower"]
    assert page["baseline"]["crossing_cases"] == 1
    assert tp.temporal_cases(log, baseline_cutoff=cutoff, direction="faster")["cases"] == []
    filtered = tp.temporal_profile(log, baseline_cutoff=cutoff, cohort={"activity": "Invoice"})
    assert filtered["cases_before_filter"] == 10 and filtered["cases"] == 2
    assert filtered["baseline"]["assessment_pair_occurrences_without_profile"] == 0
    empty = tp.temporal_profile(log, baseline_cutoff="2023-01-01T00:00:00Z")
    assert empty["baseline"]["assessment_cases_with_profile"] == 0 and empty["pairs"] == []


@pytest.mark.parametrize("cutoff", ["", "2024-01-02T00:00", "invalid", 42])
def test_cutoff_must_be_timezone_qualified(cutoff):
    with pytest.raises(EventLogWorkspaceError, match="baseline_cutoff"):
        tp.temporal_profile("not-read", baseline_cutoff=cutoff)


def test_case_evidence_pages_match_report_counts_by_pair_and_direction():
    log = _log()
    summary = tp.temporal_profile(log, zeta=0.5)
    pair = next(row for row in summary["pairs"] if (row["from"], row["to"]) == ("Order", "Ship"))
    query = {"source": "Order", "destination": "Ship", "zeta": 0.5, "limit": 2}
    for direction in ("slower", "faster"):
        rows, after = [], None
        while True:
            page = tp.temporal_cases(log, **query, direction=direction, after=after)
            assert page["deviations"] == pair[direction]
            rows.extend(page["cases"])
            after = page["next_after"]
            if after is None:
                break
        assert len(rows) == len({row["case_id"] for row in rows}) == pair[direction]
        assert all(row["direction"] == direction for row in rows)
    assert tp.temporal_cases(log, zeta=0.5)["deviating_cases"] == summary["deviating_cases"]


def test_case_evidence_keeps_a_whole_case_filter():
    log = _log()
    filtered = tp.temporal_cases(log, source="Order", destination="Invoice",
                                 cohort={"started_from": "2024-01-02T00:00:00Z"})
    assert filtered["cases_before_filter"] == 20 and filtered["cases"] == []
    late = tp.temporal_cases(log, direction="slower", source="Order", destination="Invoice")
    assert late["cases"][0]["case_id"] == "c0" and late["deviations"] == 1
    assert late["cases"][0]["strongest"]["seconds"] > late["cases"][0]["strongest"]["mean_seconds"]


@pytest.mark.parametrize("kwargs", [{"source": "A"}, {"direction": "both"}, {"limit": True}, {"after": ""}])
def test_invalid_case_pages_are_refused(kwargs):
    with pytest.raises(EventLogWorkspaceError):
        tp.temporal_cases("not-read", **kwargs)


@pytest.mark.parametrize("kwargs", [{"zeta": 0.1}, {"zeta": 11}, {"zeta": True}, {"zeta": "3"},
                                    {"min_occurrences": 1}, {"min_occurrences": 2.0}])
def test_invalid_settings_are_refused(kwargs):
    with pytest.raises(EventLogWorkspaceError):
        tp.temporal_profile(_log(), **kwargs)
