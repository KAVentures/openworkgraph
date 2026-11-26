"""Probability diagnostics against hand-calculated independent examples."""
import math
import pytest
from shared import communications_probability_reliability as r


def test_perfect_and_reversed_ranking():
    good = r.report([(1, True), (0, False)], 1, 2)
    bad = r.report([(0, True), (1, False)], 1, 2)
    assert good["auroc"] == good["average_precision"] == 1
    assert good["brier_score"] == 0
    assert good["brier_skill_against_training_prior"] == 1
    assert bad["auroc"] == 0
    assert bad["average_precision"] == .5
    assert bad["brier_skill_against_training_prior"] == -3
    assert math.isfinite(bad["log_loss_clipped"])


def test_tied_scores_and_missing():
    rows = [(0.25, True), (0.25, False), (None, True)]
    m = r.report(rows, 1, 4)
    assert m["auroc"] == m["average_precision"] == .5
    assert m["brier_score"] == .3125
    assert m["training_prior_brier_score"] == .3125
    assert m["brier_skill_against_training_prior"] == 0
    assert m["ece_fixed_10_bins"] == .25
    assert m["missing_probabilities"] == 1
    assert m["bins"][2]["count"] == 2
    assert m["bins"][2]["mean_probability"] is None
    assert sum(b["count"] for b in m["bins"]) == 2


def test_empty_and_single_class():
    empty = r.report([], 0, 0)
    assert empty["brier_score"] is None
    assert empty["auroc"] is None
    assert empty["ece_fixed_10_bins"] is None
    single = r.report([(0.8, True)] * 5, 1, 1)
    assert single["auroc"] is None
    assert single["brier_skill_against_training_prior"] is None
    assert single["bins"][8]["observed_positive_rate"] == 1
    assert single["bins"][8]["mean_probability"] == pytest.approx(.8)
    assert not single["sufficient_scored_support"]


def test_tie_group_oracle():
    rows = [(0.9, True), (0.8, False), (0.8, True), (0.1, False)]
    m = r.report(rows, 2, 4)
    assert m["auroc"] == .875
    assert m["average_precision"] == pytest.approx(.5 + .5*2/3)


def test_supported_macro_excludes_untrained_and_incomplete():
    rows = [({"A"} if i < 5 else set(), {}, {}, {"A": .9 if i < 5 else .1})
            for i in range(10)]
    training = [{"labels": ["A"]}, {"labels": []}]
    result = r.summarize(rows, ["A", "B"], {"A"}, training)
    assert result["macro_supported_intents"] == 1
    assert result["macro"]["auroc"] == 1
    assert result["per_intent"][1]["metrics"] is None
    rows[0][3].clear()
    assert r.summarize(rows, ["A"], {"A"}, training)["macro_supported_intents"] == 0


def test_invalid_probability():
    with pytest.raises(ValueError, match="bounded"):
        r.report([(float("nan"), True)], 1, 2)
