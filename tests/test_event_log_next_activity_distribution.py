"""Full next activity distribution scores with independently computed oracles."""
from collections import Counter
from math import log
import pytest

from server import event_log_next_activity_distribution as d


def test_full_distribution_brier_log_loss_and_unseen():
    p=d.Distribution(Counter({"A":4,"B":3,"C":2,None:1}))
    a=p.scores("C",False)
    assert a["actual_probability"]==.2
    assert a["brier"]==pytest.approx((.4**2+.3**2+(.2-1)**2+.1**2,)*2)
    assert a["clipped_log_loss"]==pytest.approx((-log(.2),)*2)
    unseen=p.scores("D",False)
    assert unseen["actual_probability"]==0
    assert unseen["brier"]==pytest.approx((1.3,1.3))
    assert unseen["clipped_log_loss"]==pytest.approx((-log(d.EPSILON),)*2)


def test_missing_outcome_never_scores_end_and_bounds_contain_all_labels():
    p=d.Distribution(Counter({"A":1,"B":2,None:7}))
    missing=p.scores(None,True)
    for label in ("A","B","unseen"):
        resolved=p.scores(label,False)
        for k in ("brier","clipped_log_loss"):
            assert missing[k][0]<=resolved[k][0]<=missing[k][1]
    assert missing["brier"][0]==pytest.approx(p.scores("B",False)["brier"][0])
    assert missing["brier"][1]==p.scores("unseen",False)["brier"][1]
    end=d.Distribution(Counter({None:1})).scores(None,True)
    assert end["brier"]==(2.,2.)
    assert end["clipped_log_loss"]==(-log(d.EPSILON),)*2


def test_accumulator_coverage_and_identical_counter_pair_cancellation():
    r=d.Review()
    a=Counter({"A":2,"B":1})
    r.add({"model":a,"overall_baseline":a},"A",False)
    r.add({"model":a,"overall_baseline":a},None,True)
    r.add({},None,True)
    out=r.report(3)
    model=out["estimators"]["model"]
    assert model["predicted"]==2 and model["resolved"]==1 and model["abstained"]==1
    assert out["distinct_counter_profiles"]==1
    pair=out["paired_comparisons"]["overall_baseline"]
    assert pair["paired_predictions"]==2 and pair["paired_resolved"]==1
    for key in ("brier","clipped_log_loss"):
        assert pair[key]["paired_population_mean_difference_bounds"]=={"lower":0.,"upper":0.}
    assert out["estimators"]["last_activity_baseline"]["brier"]["resolved_mean"] is None


def test_pair_outer_bounds_cover_joint_actual_choices():
    r=d.Review()
    a=Counter({"A":3,"B":1,None:1})
    b=Counter({"A":1,"C":2,None:1})
    r.add({"model":a,"overall_baseline":b},None,True)
    pair=r.report(1)["paired_comparisons"]["overall_baseline"]
    for label in ("A","B","C","unseen"):
        x=d.Distribution(a).scores(label,False)
        y=d.Distribution(b).scores(label,False)
        for k in ("brier","clipped_log_loss"):
            bound=pair[k]["paired_population_mean_difference_bounds"]
            assert bound["lower"]<=x[k][0]-y[k][0]<=bound["upper"]


def test_cached_profiles_and_zero_probability_count():
    r=d.Review();a=Counter({"A":1})
    for _ in range(100):
        r.add({"model":a},"B",False)
    out=r.report(100)
    assert out["distinct_counter_profiles"]==1
    assert out["estimators"]["model"]["resolved_zero_actual_probability"]==100
    assert out["estimators"]["model"]["brier"]["resolved_mean"]==2
