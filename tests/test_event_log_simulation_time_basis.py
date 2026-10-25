"""Mean or median measured activity times in what-if simulation (#370 R14)."""
from __future__ import annotations

import math

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_simulation_validation import _log


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def test_calibration_reports_the_mean_gap_next_to_the_median():
    b = sim.calibrate(_log(exponential=True, cases=2000))["activities"]["B"]
    # B's gap is exponential with a 2 h mean: its median is 2 h · ln 2.
    assert b["mean_minutes"] == pytest.approx(120, rel=0.08)
    assert b["minutes"] == pytest.approx(120 * math.log(2), rel=0.1)


def test_the_mean_basis_removes_the_exponential_shortfall_in_held_out_validation():
    for seed in (1, 2, 3):
        log = _log(exponential=True, seed=seed)
        median = sim.validate(log, {})["cycle_time_seconds"]["relative_error"]["mean"]
        mean = sim.validate(log, {"activity_time_basis": "mean"})
        assert mean["activity_time_basis"] == "mean"
        assert abs(mean["cycle_time_seconds"]["relative_error"]["mean"]) < 0.12 < abs(median)
        assert any("mean gap" in a for a in mean["assumptions"])
        assert not any("median gap" in a for a in mean["assumptions"])


def test_what_if_uses_one_basis_for_both_runs_and_explicit_minutes_still_win():
    log = _log(exponential=True)
    cal = sim.calibrate(log)["activities"]
    result = sim.what_if(log, {"cases": 500, "activity_time_basis": "mean"})
    assert result["scenario"]["activity_time_basis"] == "mean"
    assert result["scenario"]["activities"]["B"]["minutes"] == cal["B"]["mean_minutes"]
    # The baseline measures times the same way, so an unchanged scenario simulates identically.
    assert result["baseline"]["cycle_time_seconds"] == result["result"]["cycle_time_seconds"]
    explicit = sim.what_if(log, {"cases": 500, "activity_time_basis": "mean", "activities": {"B": {"minutes": 7}}})
    assert explicit["scenario"]["activities"]["B"]["minutes"] == 7
    default = sim.what_if(log, {"cases": 500})
    assert default["scenario"]["activity_time_basis"] == "median"
    assert default["scenario"]["activities"]["B"]["minutes"] == cal["B"]["minutes"]
    assert any(a.startswith("Calibrated activity times are the median") for a in default["assumptions"])


@pytest.mark.parametrize("call", [
    lambda log: sim.what_if(log, {"cases": 100, "activity_time_basis": "mode"}),
    lambda log: sim.validate(log, {"activity_time_basis": 1}),
])
def test_unknown_bases_are_refused(call):
    with pytest.raises(workspace.EventLogWorkspaceError, match="activity_time_basis"):
        call(_log(cases=200))
