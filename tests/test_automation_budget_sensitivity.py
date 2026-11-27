"""Independent exhaustive oracle for portfolio frontier and sensitivity."""
from decimal import Decimal
from itertools import combinations
import random

import pytest

from server import automation_budget_scenario as scenario
from server import automation_pipeline as pipeline
from test_automation_budget_scenario import store, idea, request


def oracle(items, cap, count_limit, required=()):
    rows = []
    for size in range(len(items)+1):
        for chosen in combinations(items, size):
            ids = {i["idea_id"] for i, _ in chosen}
            if size > count_limit or not set(required) <= ids:
                continue
            if any(not set(i["planning"]["depends_on"]) <= ids for i, _ in chosen):
                continue
            cost = sum((Decimal(str(i["planning"]["budget"]["amount"])) for i, _ in chosen), Decimal(0))
            if cost > Decimal(str(cap)):
                continue
            rows.append((-sum(points for _, points in chosen), cost, size, tuple(sorted(ids))))
    return sorted(rows)


def packet(row):
    if row is None:
        return None
    return ( -row["priority_points"], Decimal(row["budget_used"]),
             row["selected_count"], tuple(row["selected_idea_ids"]))


def assert_oracle(result, rows, targets):
    d = result["sensitivity"]
    assert d["feasible_subset_count"] == result["feasible_subsets"] == len(rows)
    assert d["frontier_complete_within_reviewed_cap"]
    assert not d["constraints_relaxed"] and not d["above_cap_extrapolated"]
    expected = []
    for row in rows:
        points, cost, *_ = row
        if any(other[0] <= points and other[1] <= cost and
               (other[0] < points or other[1] < cost) for other in rows):
            continue
        if not any(other[:2] == row[:2] for other in expected):
            expected.append(row)
    assert [packet(x) for x in d["frontier"]] == sorted(expected, key=lambda x:x[1])
    optimal = [r for r in rows if r[0] == rows[0][0]] if rows else []
    cheap = [r for r in optimal if r[1] == rows[0][1]] if rows else []
    assert d["maximum_priority_subset_count"] == len(optimal)
    assert d["maximum_priority_min_cost_subset_count"] == len(cheap)
    for c in d["candidate_sensitivity"]:
        wid = c["idea_id"]
        with_id = [r for r in rows if wid in r[3]]
        without = [r for r in rows if wid not in r[3]]
        assert c["feasible_with_count"] == len(with_id)
        assert c["feasible_without_count"] == len(without)
        assert c["max_priority_with_count"] == sum(wid in r[3] for r in optimal)
        assert c["max_priority_min_cost_with_count"] == sum(wid in r[3] for r in cheap)
        for key, group in (("force_include", with_id), ("force_exclude", without)):
            v = c[key]
            if not group:
                assert v["status"] == "infeasible" and v["best"] is None
                assert v["priority_loss"] is None
            else:
                assert v["status"] == "feasible"
                assert packet(v["best"]) == group[0]
                assert v["priority_loss"] == group[0][0] - rows[0][0]
                assert Decimal(v["additional_budget_at_best"]) == group[0][1]-rows[0][1]
    assert [x["target_priority_points"] for x in d["priority_targets"]] == sorted(targets)
    for row in d["priority_targets"]:
        possible = [r for r in rows if -r[0] >= row["target_priority_points"]]
        if possible:
            assert Decimal(row["minimum_budget"]) == min(r[1] for r in possible)
            assert packet(row["witness"]) in possible
        else:
            assert row["minimum_budget"] is None and row["witness"] is None


def test_exact_frontier_dependencies_required_and_decimal_costs():
    a = idea("A", .1)
    b = idea("B", .2, [a])
    c = idea("C", .3)
    d = idea("D", 0)
    items = [(a, 4), (b, 20), (c, 24), (d, 0)]
    targets = [0, 4, 20, 24, 25]
    spec = request(items, limit=.3, max_selected=3, include_sensitivity=True, priority_targets=targets)
    result = scenario.solve(spec)
    assert_oracle(result, oracle(items, .3, 3), targets)
    assert result["sensitivity"]["maximum_priority_subset_count"] == 4
    assert result["sensitivity"]["maximum_priority_min_cost_subset_count"] == 4
    assert scenario.solve(spec) == result
    assert scenario.inventory()["snapshot_sha256"] == spec["snapshot_sha256"]


def test_mandatory_dependency_and_infeasible_target():
    a = idea("A", 10)
    b = idea("B", 20, [a])
    c = idea("C", 100)
    items = [(a, 0), (b, 50), (c, 100)]
    required = [b["idea_id"]]
    result = scenario.solve(request(items, limit=30, required=required,
        include_sensitivity=True, priority_targets=[50, 51]))
    assert_oracle(result, oracle(items, 30, 18, required), [50, 51])
    d = {x["idea_id"]:x for x in result["sensitivity"]["candidate_sensitivity"]}
    assert d[a["idea_id"]]["membership_in_all_feasible"] == "all"
    assert d[b["idea_id"]]["force_exclude"]["status"] == "infeasible"
    assert d[c["idea_id"]]["membership_in_all_feasible"] == "none"


def test_empty_feasible_population_is_not_a_zero_cost_solution():
    a = idea("A", 100)
    result = scenario.solve(request([(a, 50)], limit=0, required=[a["idea_id"]],
        include_sensitivity=True, priority_targets=[0]))
    assert_oracle(result, [], [0])
    d = result["sensitivity"]
    assert d["maximum_priority_points"] is None
    assert d["minimum_cost_at_maximum_priority"] is None
    assert d["candidate_sensitivity"][0]["membership_in_all_feasible"] == "no_feasible_portfolio"


def test_missing_dependency_and_mismatched_budget_not_silently_admitted():
    a = idea("Unselected dependency", 5)
    b = idea("Blocked", 0, [a])
    c = idea("Wrong currency", 0, currency="USD")
    d = idea("Free", 0)
    result = scenario.solve(request([(b, 100), (c, 100), (d, 1)],
        limit=0, include_sensitivity=True))
    by_id = {x["idea_id"]: x for x in result["sensitivity"]["candidate_sensitivity"]}
    assert by_id[b["idea_id"]]["feasible_with_count"] == 0
    assert by_id[c["idea_id"]]["force_include"]["status"] == "infeasible"
    assert by_id[d["idea_id"]]["membership_among_max_priority"] == "all"


@pytest.mark.parametrize("seed", range(8))
def test_random_small_dag_against_independent_enumeration(seed):
    rng = random.Random(seed)
    items = []
    for i in range(7):
        deps = [r for r, _ in items if rng.random() < .18]
        record = idea(str(i), rng.randrange(0, 9)/10, deps)
        items.append((record, rng.randrange(0, 8)))
    cap = rng.randrange(0, 21)/10
    count = rng.randrange(1, 8)
    required = [items[-1][0]["idea_id"]] if seed % 2 else []
    targets = [0, 5, 15, 25]
    result = scenario.solve(request(items, limit=cap, max_selected=count, required=required,
        include_sensitivity=True, priority_targets=targets))
    assert_oracle(result, oracle(items, cap, count, required), targets)


@pytest.mark.parametrize("overrides", [
    {"include_sensitivity": 1}, {"priority_targets": [1]},
    {"include_sensitivity": True, "priority_targets": [True]},
    {"include_sensitivity": True, "priority_targets": [-1]},
    {"include_sensitivity": True, "priority_targets": [1801]},
    {"include_sensitivity": True, "priority_targets": [1, 1]},
    {"include_sensitivity": True, "priority_targets": list(range(13))},
])
def test_bad_sensitivity_inputs(overrides):
    a = idea("A", 1)
    with pytest.raises(pipeline.PipelineError):
        scenario.solve(request([(a, 5)], **overrides))


def test_legacy_response_and_objective_unchanged():
    a = idea("A", 1)
    b = idea("B", 2)
    legacy = scenario.solve(request([(a, 10), (b, 20)], limit=2))
    disabled = scenario.solve(request([(a, 10), (b, 20)], limit=2,
                                      include_sensitivity=False, priority_targets=[]))
    extended = scenario.solve(request([(a, 10), (b, 20)], limit=2, include_sensitivity=True))
    assert legacy == disabled
    assert "sensitivity" not in legacy
    for key in ("alternatives", "best_by_selected_count", "rejected_by_first_reason", "feasible_subsets"):
        assert legacy[key] == extended[key]
