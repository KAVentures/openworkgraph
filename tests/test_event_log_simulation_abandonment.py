"""Explicit queue abandonment in R14 what-if simulations.

These tests do not equate assumed timeouts with observed business outcomes.
"""
from __future__ import annotations

from io import BytesIO
import math

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace
from test_event_log_analytics import _claims_csv


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _baseline():
    return {
        "arrivals_per_hour": 3600.0,
        "paths": [{"activities": ["Serve"], "weight": 1.0}],
        "activities": {"Serve": {"minutes": 2, "mean_minutes": 2, "resources": 1}},
        "distribution": "fixed",
    }


def _model(timeout=None, **settings):
    changes = {"cases": 200, "seed": 37, "distribution": "fixed", **settings}
    if timeout is not None:
        changes["activities"] = {"Serve": {"queue_timeout_minutes": timeout}}
    return sim.parse_scenario(changes, _baseline())


def test_no_timeout_remains_byte_for_byte_equivalent_to_existing_scenario():
    original = _model()
    explicit_original = sim.parse_scenario(
        {"cases": 200, "seed": 37, "distribution": "fixed", "activities": {"Serve": {}}},
        _baseline(),
    )
    assert original == explicit_original
    a, b = sim.simulate(original), sim.simulate(explicit_original)
    assert a == b
    assert "cases_abandoned" not in a
    assert "abandonment_by_activity" not in a


def test_overloaded_queue_abandons_waiters_without_falsely_completing_them():
    model = _model(timeout=0.01)
    out = sim.simulate(model)
    assert out["cases_abandoned"] > 100
    assert out["cases_abandoned"] + out["cases_measured"] + int(model["cases"] * sim.WARM_UP_SHARE) >= model["cases"]
    assert out["abandonment_by_activity"] == [{
        "activity": "Serve",
        "cases_abandoned": out["cases_abandoned_after_warmup"],
        "queue_timeout_minutes": 0.01,
    }]
    assert out["abandonment_rate_after_warmup"] == pytest.approx(
        out["cases_abandoned_after_warmup"] / (model["cases"] - int(model["cases"] * sim.WARM_UP_SHARE)),
        abs=1e-6,
    )
    assert 0 < out["abandonment_rate_after_warmup"] <= 1
    assert out["abandonment_is_simulated_not_observed"] is True


class _OneSecondArrivals:
    """Deterministic clock used only to check the event ordering boundary."""

    def next_arrival(self, now, rate, draw):
        return now + 1.0

    def local(self, now):
        from datetime import datetime, timedelta, timezone
        return datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=now)

    def describe(self):
        return {"test_clock": "one_second_between_arrivals"}


def test_completion_wins_when_queue_deadline_is_exactly_equal():
    model = _model(timeout=1 / 60)
    model["cases"] = 2
    model["activities"]["Serve"]["minutes"] = 2 / 60
    model["arrival_pattern"] = _OneSecondArrivals()
    out = sim.simulate(model)
    assert out["cases_abandoned"] == 0
    assert out["cases_measured"] == 2
    # A deadline strictly BEFORE the completion must instead abandon the waiter.
    model["activities"]["Serve"]["queue_timeout_minutes"] = 0.5 / 60
    dropped = sim.simulate(model)
    assert dropped["cases_abandoned"] == 1
    assert dropped["cases_measured"] == 1


def test_pool_and_priority_queue_support_selected_per_activity_timeouts():
    base = _baseline()
    base["paths"] = [{"activities": ["Serve", "Check"], "weight": 1.0}]
    base["activities"]["Check"] = {"minutes": 3, "mean_minutes": 3, "resources": 1}
    choices = {
        "cases": 150,
        "seed": 9,
        "distribution": "fixed",
        "activities": {"Check": {"queue_timeout_minutes": 0.01}},
        "pools": {"Shared": {"resources": 1, "activities": ["Serve", "Check"]}},
        "priorities": [{"name": "Urgent", "share": 0.5, "rank": 0},
                       {"name": "Routine", "share": 0.5, "rank": 1}],
    }
    model = sim.parse_scenario(choices, base)
    out = sim.simulate(model)
    assert out["cases_abandoned"] > 0
    assert len(out["abandonment_by_activity"]) == 1
    assert out["abandonment_by_activity"][0]["activity"] == "Check"
    assert len(out["priority_classes"]) == 2
    assert len(out["pools"]) == 1
    assert out["cases_abandoned_after_warmup"] <= out["cases_abandoned"]
    assert sum(x["cases_abandoned"] for x in out["abandonment_by_activity"]) == out["cases_abandoned_after_warmup"]


@pytest.mark.parametrize("invalid", [-0.01, 525601, True, "2", None, float("inf"), float("nan")])
def test_invalid_queue_deadlines_are_refused(invalid):
    with pytest.raises(workspace.EventLogWorkspaceError, match="queue_timeout_minutes"):
        sim.parse_scenario({"activities": {"Serve": {"queue_timeout_minutes": invalid}}}, _baseline())


def test_batch_and_individual_abandonment_are_deliberately_incompatible():
    with pytest.raises(workspace.EventLogWorkspaceError, match="cannot also abandon"):
        sim.parse_scenario({"activities": {"Serve": {"queue_timeout_minutes": 1,
                          "batch": {"size": 2}}}}, _baseline())


def _log():
    return workspace.import_log(BytesIO(_claims_csv().encode()), name="claims", source_format="csv", mapping={
        "case_column": "case", "activity_column": "activity", "timestamp_column": "time",
        "resource_column": "who",
    })["log_id"]


def test_existing_authenticated_simulation_backend_exposes_scenario_only_abandonment():
    # what_if is the implementation called by the already authenticated /simulate route.
    log_id = _log()
    last = sim.calibrate(log_id)["paths"][0]["activities"][-1]
    result = sim.what_if(log_id, {
        "cases": 1200, "seed": 7, "distribution": "fixed", "arrivals_per_hour": 1000,
        "activities": {last: {"minutes": 90, "resources": 1, "queue_timeout_minutes": 0.01}},
    })
    assert "cases_abandoned" not in result["baseline"]
    assert result["scenario"]["activities"][last]["queue_timeout_minutes"] == 0.01
    assert result["result"]["cases_abandoned"] > 0
    assert result["result"]["abandonment_is_simulated_not_observed"] is True
    assert any("queue_timeout_minutes" in line for line in result["assumptions"])
    assert result["simulated_not_observed"] is True
    assert result["execution_authorized"] is False
    assert result["forecast_or_guarantee"] is False
