"""Independent exact oracles for censored prediction score bounds."""
from itertools import product
import pytest
from server.event_log_prospective_scores import paired_error_bounds, proper_time_scores


def row(m=10,b=20,l=5,u=15):
    return {"median_remaining_seconds":m,"naive_median_remaining_seconds":b,
            "remaining_p10_seconds":l,"remaining_p90_seconds":u}


def test_paired_bounds_match_enumeration():
    finished=[(row(),12,None)]
    opened=[row(),row(30,10)]
    missing=[row(8,12)]
    result=paired_error_bounds(finished,opened,missing,15)
    candidates=[]
    for t in product([15,20,25,30,100],[15,20,25,30,100],[0,8,10,12,100]):
        actuals=[12,*t]
        frozen=[finished[0][0],*opened,*missing]
        ds=[abs(r["median_remaining_seconds"]-v)-abs(r["naive_median_remaining_seconds"]-v)
            for r,v in zip(frozen,actuals)]
        candidates.append((sum(ds)/4,sum(d<0 for d in ds),sum(d>0 for d in ds),sum(d==0 for d in ds)))
    for index, lo, hi in [
        (0,"eventual_mean_absolute_error_difference_lower_seconds","eventual_mean_absolute_error_difference_upper_seconds"),
        (1,"model_better_cases_lower","model_better_cases_upper"),
        (2,"baseline_better_cases_lower","baseline_better_cases_upper"),
        (3,"tied_cases_lower","tied_cases_upper")]:
        assert result[lo]==min(c[index] for c in candidates)
        assert result[hi]==max(c[index] for c in candidates)


def test_open_case_comparison_can_be_known_before_completion():
    r=paired_error_bounds([], [row(30,10)], [], 40)
    assert r["comparison"]=="model_always_lower_error"
    assert r["eventual_mean_absolute_error_difference_lower_seconds"]==-20
    assert r["eventual_mean_absolute_error_difference_upper_seconds"]==-20
    assert r["model_better_cases_lower"]==1
    assert paired_error_bounds([], [row(10,10)], [], 0)["comparison"]=="equal_error"


def test_missing_pair_not_silently_imputed():
    r=paired_error_bounds([(row(b=None),12,None)],[],[row(b=float("nan"))],0)
    assert r["paired_cases"]==0 and r["cases_without_valid_pair"]==2
    assert r["comparison"]=="unavailable"
    assert r["eventual_mean_absolute_error_difference_upper_seconds"] is None


def test_quantile_and_interval_score_hand_oracle():
    f=[(row(),2,None),(row(),20,None),(row(),10,None)]
    r=proper_time_scores(f,[row()],[row()],25)
    i=r["interval_80"]
    # widths: 10 each; early penalty 30; late penalty 50;
    # open minimum late penalty 100; missing minimum penalty zero.
    assert i["finished_mean_interval_score_seconds"]==pytest.approx(110/3)
    assert i["eventual_mean_interval_score_lower_seconds"]==46
    assert i["eventual_mean_interval_score_upper_seconds"] is None
    assert i["finished_early_misses"]==i["finished_late_misses"]==i["censored_known_late_misses"]==1
    q=r["quantiles"][1]
    assert q["finished_mean_pinball_seconds"]==3
    assert q["eventual_mean_pinball_lower_seconds"]==3.3
    assert q["eventual_mean_pinball_upper_seconds"] is None


def test_complete_scores_are_point_identified_and_boundaries_inclusive():
    r=proper_time_scores([(row(),5,None),(row(),15,None)],[],[],0)
    i=r["interval_80"]
    assert i["finished_mean_interval_score_seconds"]==10
    assert i["eventual_mean_interval_score_lower_seconds"]==i["eventual_mean_interval_score_upper_seconds"]==10
    assert not i["finished_early_misses"] and not i["finished_late_misses"]
    for q in r["quantiles"]:
        assert q["eventual_mean_pinball_lower_seconds"]==q["eventual_mean_pinball_upper_seconds"]


def test_invalid_range_and_empty_populations_are_explicit():
    r=proper_time_scores([], [row(l=20,u=10)],[], 10)
    assert r["interval_80"]["ineligible_cases"]==1
    assert r["interval_80"]["eventual_mean_interval_score_lower_seconds"] is None
    assert proper_time_scores([],[],[],0)["quantiles"][0]["finished_mean_pinball_seconds"] is None
