"""R14: priority-sensitive queue-abandonment what-if outcomes.

Assertions explicitly distinguish simulated abandonment from recorded business
outcomes, and base rates on all arrivals, not survivor-only cycle-time metrics.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from server import event_log_simulation as sim
from server import event_log_workspace as workspace


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)


def _baseline():
    return {
        "arrivals_per_hour": 3600.0,
        "paths": [{"activities": ["Review"], "weight": 1.0}],
        "activities": {"Review": {"minutes": 2.0, "mean_minutes": 2.0, "resources": 1}},
        "distribution": "fixed",
    }


def _priority_scenario(*, timeout=True, priorities=True, cases=400, seed=41):
    changes = {"cases": cases, "seed": seed, "distribution": "fixed"}
    if timeout:
        changes["activities"] = {"Review": {"queue_timeout_minutes": 0.01}}
    if priorities:
        changes["priorities"] = [
            {"name": "Urgent", "share": 0.4, "rank": 0},
            {"name": "Routine", "share": 0.6, "rank": 1},
        ]
    return sim.parse_scenario(changes, _baseline())


def test_aggregates_count_every_post_warmup_arrival_once_not_survivors_only():
    model = _priority_scenario()
    out = sim.simulate(model)
    rows = out["priority_outcomes"]
    assert len(rows) == 2
    assert [x["name"] for x in rows] == ["Urgent", "Routine"]
    assert [x["rank"] for x in rows] == [0, 1]
    assert out["priority_abandonment_is_simulated_not_observed"] is True
    assert out["priority_outcomes_not_a_fairness_or_causal_estimate"] is True
    after_warmup = model["cases"] - int(model["cases"] * sim.WARM_UP_SHARE)
    assert sum(x["post_warmup_arrivals"] for x in rows) == after_warmup
    assert sum(x["completed"] for x in rows) == out["cases_measured"]
    assert sum(x["abandoned"] for x in rows) == out["cases_abandoned_after_warmup"]
    assert sum(x["unresolved"] for x in rows) == (
        after_warmup - out["cases_measured"] - out["cases_abandoned_after_warmup"]
    )
    assert out["cases_abandoned"] > 0
    for row, class_cycle in zip(rows, out["priority_classes"]):
        assert row["completed"] == class_cycle["cases_measured"]
        assert row["post_warmup_arrivals"] == row["completed"] + row["abandoned"] + row["unresolved"]
        assert row["abandonment_rate"] == pytest.approx(
            row["abandoned"] / row["post_warmup_arrivals"], abs=1e-6
        )
        assert row["completion_rate"] == pytest.approx(
            row["completed"] / row["post_warmup_arrivals"], abs=1e-6
        )
        assert row["selected_completions_only_for_cycle_time"] is True
        assert row["abandoned_by_activity"] == [
            {"activity": "Review", "cases_abandoned": row["abandoned"]}
        ]
    assert sum(x["abandoned"] for x in rows) == out["abandonment_by_activity"][0]["cases_abandoned"]


def test_priority_classes_without_timeout_preserve_previous_result_keys():
    model = _priority_scenario(timeout=False)
    out = sim.simulate(model)
    assert "priority_classes" in out
    assert "priority_outcomes" not in out
    assert "priority_outcome_basis" not in out
    assert "priority_abandonment_is_simulated_not_observed" not in out
    assert "cases_abandoned" not in out


def test_timeout_without_priority_does_not_invent_priority_classes():
    out = sim.simulate(_priority_scenario(priorities=False))
    assert out["cases_abandoned"] > 0
    assert "priority_classes" not in out
    assert "priority_outcomes" not in out


def test_zero_abandonment_is_reported_as_zero_not_missing():
    model = _priority_scenario(cases=80)
    model["activities"]["Review"]["queue_timeout_minutes"] = 525600.0
    out = sim.simulate(model)
    assert out["cases_abandoned"] == 0
    assert len(out["priority_outcomes"]) == 2
    assert all(x["abandoned"] == 0 and x["abandonment_rate"] == 0
               for x in out["priority_outcomes"])
    assert sum(x["completed"] for x in out["priority_outcomes"]) == out["cases_measured"]


def test_shared_pool_two_distinct_timeout_activities_match_global_totals():
    base = _baseline()
    base["paths"] = [{"activities": ["Review", "Pay"], "weight": 1.0}]
    base["activities"]["Pay"] = {"minutes": 3.0, "mean_minutes": 3.0, "resources": 1}
    model = sim.parse_scenario({
        "cases": 320, "seed": 47, "distribution": "fixed",
        "activities": {"Review": {"queue_timeout_minutes": 0.05},
                       "Pay": {"queue_timeout_minutes": 0.01}},
        "pools": {"Shared": {"resources": 1, "activities": ["Review", "Pay"]}},
        "priorities": [
            {"name": "Urgent", "share": 0.2, "rank": 0},
            {"name": "Other", "share": 0.8, "rank": 1},
        ],
    }, base)
    out = sim.simulate(model)
    assert out["cases_abandoned"] > 0
    total_by_activity = {
        x["activity"]: x["cases_abandoned"] for x in out["abandonment_by_activity"]
    }
    assert set(total_by_activity) == {"Review", "Pay"}
    for activity in total_by_activity:
        assert sum(
            next(x for x in row["abandoned_by_activity"]
                 if x["activity"] == activity)["cases_abandoned"]
            for row in out["priority_outcomes"]
        ) == total_by_activity[activity]
    assert sum(x["abandoned"] for x in out["priority_outcomes"]) == out["cases_abandoned_after_warmup"]


def test_existing_what_if_api_includes_priority_outcome_accounting_without_promoting_to_forecast():
    csv = "\n".join(
        ["case,activity,time"] + [
            f"c{i},Review,2026-01-{1 + (i % 20):02d}T{8 + (i % 12):02d}:00:00Z"
            for i in range(100)
        ]
    )
    log_id = workspace.import_log(BytesIO(csv.encode()), name="priority trial",
                                  source_format="csv", mapping={
                                      "case_column": "case",
                                      "activity_column": "activity",
                                      "timestamp_column": "time",
                                  })["log_id"]
    result = sim.what_if(log_id, {
        "cases": 250, "seed": 9, "distribution": "fixed",
        "arrivals_per_hour": 3600,
        "activities": {"Review": {"minutes": 2.0, "resources": 1,
                                  "queue_timeout_minutes": 0.01}},
        "priorities": [{"name": "Urgent", "share": 0.5, "rank": 0},
                       {"name": "Routine", "share": 0.5, "rank": 1}],
    })
    outcome = result["result"]
    assert outcome["cases_abandoned"] > 0
    assert len(outcome["priority_outcomes"]) == 2
    assert all(row["post_warmup_arrivals"] >= row["completed"] + row["abandoned"]
               for row in outcome["priority_outcomes"])
    assert result["simulated_not_observed"] is True
    assert result["execution_authorized"] is False
    assert result["forecast_or_guarantee"] is False
