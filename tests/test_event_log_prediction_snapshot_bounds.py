"""Finite-population bounds and paired comparisons are independently testable."""
from itertools import product

from server.event_log_prediction_snapshot_bounds import coverage_bounds, outcome_bounds, paired_time_score


def item(median=10, low=5, high=15, naive=12, p=.2, b=.5):
    return {"median_remaining_seconds": median, "remaining_p10_seconds": low,
            "remaining_p90_seconds": high, "naive_median_remaining_seconds": naive,
            "outcome_probability": p, "base_rate": b}


def test_coverage_bounds_partition_every_frozen_case():
    finished = [(item(), 10, True), (item(), 20, False)]
    opened = [item(), item(high=30)]
    result = coverage_bounds(finished, opened, 1, 20)
    assert result["frozen_cases"] == 5
    assert result["known_inside_range"] == 1
    assert result["known_outside_range"] == 2
    assert result["range_membership_unknown"] == 2
    assert result["still_open_known_misses"] == 1
    assert result["eventual_range_coverage_lower"] == .2
    assert result["eventual_range_coverage_upper"] == .6
    assert result["eventual_mae_lower_bound_seconds"] == 6
    assert result["eventual_mae_upper_bound_seconds"] is None
    assert coverage_bounds([], [], 0, 0)["eventual_range_coverage_lower"] is None
    boundary = coverage_bounds([], [item()], 0, 15)
    assert boundary["still_open_known_misses"] == 0
    complete = coverage_bounds(finished, [], 0, 20)
    assert complete["eventual_mae_lower_bound_seconds"] == complete["eventual_mae_upper_bound_seconds"] == 5


def test_time_comparison_excludes_unpaired_baselines_and_counts_case_wins():
    finished = [(item(), 10, True), (item(), 12, False), (item(), 11, True),
                (item(naive=None), 100, False)]
    score = paired_time_score(finished)
    assert score["paired_cases"] == 3 and score["finished_cases_without_baseline"] == 1
    assert score["model_mae_seconds"] == score["baseline_mae_seconds"] == 1
    assert score["model_minus_baseline_mae_seconds"] == 0
    assert score["model_better_cases"] == score["baseline_better_cases"] == score["tied_cases"] == 1
    assert paired_time_score([(item(naive=None), 10, True)]) is None


def test_outcome_bounds_match_exhaustive_binary_assignments():
    frozen = [item(p=.1,b=.5),item(p=.8,b=.2),item(p=.3,b=.7),item(p=.9,b=.1)]
    finished = [(frozen[0],10,False),(frozen[1],10,None),
                (item(p=.9,b=None),10,True)]
    unresolved = frozen[2:]
    result = outcome_bounds(finished, unresolved)
    assert result["paired_frozen_cases"] == 4 and result["known_outcome_cases"] == 1
    assert result["unknown_outcome_cases"] == 3 and result["frozen_cases_without_paired_risk"] == 1
    model, delta = [], []
    for labels in product((0,1),repeat=3):
        y = (0,*labels)
        scores = [(row["outcome_probability"]-label)**2 for row,label in zip(frozen,y)]
        baseline = [(row["base_rate"]-label)**2 for row,label in zip(frozen,y)]
        model.append(sum(scores)/4)
        delta.append((sum(scores)-sum(baseline))/4)
    assert result["eventual_model_brier_lower"] == round(min(model),5)
    assert result["eventual_model_brier_upper"] == round(max(model),5)
    assert result["eventual_model_minus_baseline_brier_lower"] == round(min(delta),5)
    assert result["eventual_model_minus_baseline_brier_upper"] == round(max(delta),5)
    assert outcome_bounds([], [item(p=None)],) is None
