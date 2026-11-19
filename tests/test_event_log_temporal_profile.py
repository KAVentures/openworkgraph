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


@pytest.mark.parametrize("kwargs", [{"zeta": 0.1}, {"zeta": 11}, {"zeta": True}, {"zeta": "3"},
                                    {"min_occurrences": 1}, {"min_occurrences": 2.0}])
def test_invalid_settings_are_refused(kwargs):
    with pytest.raises(EventLogWorkspaceError):
        tp.temporal_profile(_log(), **kwargs)
