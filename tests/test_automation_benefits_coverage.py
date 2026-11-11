"""Coverage-aware duration-weighted realized-value comparison (R16).

The new exposure comparator refuses duplicate coverage and empty case
denominators while the legacy equal-weight summary is preserved.
"""
from datetime import date, timedelta

import pytest

from server import automation_benefits as benefits
from server import automation_pipeline as pipeline


@pytest.fixture(autouse=True)
def isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DATA_DIR", tmp_path)


def entry(kind, start, days, cases, *, minutes=None, hours=None, source="entered"):
    row = {"period": kind, "start": start.isoformat(),
           "end": (start + timedelta(days=days - 1)).isoformat(),
           "cases": cases, "source": source}
    if minutes is not None:
        row["minutes_per_case"] = minutes
    if hours is not None:
        row["total_hours"] = hours
    return row


def implemented():
    idea = pipeline.create({"title": "Invoice reconciliation"})
    idea = pipeline.transition(idea["idea_id"], {"to": "assessing", "expected_revision": idea["revision"]})
    answers = {q: 5 for q in pipeline.EASE_QUESTIONS}
    idea = pipeline.assess(idea["idea_id"], {"ease": answers, "cases_per_month": 300,
                                            "minutes_per_case": 10, "eligible_share": 0.5,
                                            "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "approved", "expected_revision": idea["revision"]})
    idea = pipeline.transition(idea["idea_id"], {"to": "implemented", "expected_revision": idea["revision"]})
    return idea["idea_id"]


def test_disjoint_unequal_periods_weight_days_cases_and_hours_not_period_count():
    ident = implemented()
    today = date.today()
    # A one-day 20-hour period must not weigh as much as a 30-day 30-hour period.
    benefits.record(ident, entry("baseline", today - timedelta(days=90), 1, 10, minutes=120))
    benefits.record(ident, entry("baseline", today - timedelta(days=80), 30, 300, minutes=6))
    result = benefits.record(ident, entry("after", today, 1, 10, minutes=4, source="system_report"))
    adjusted = result["coverage_adjusted"]
    assert adjusted["status"] == "ready" and not adjusted["overlap_pairs"]
    assert adjusted["coverage"]["baseline"] == {
        "periods": 2, "calendar_days_reported": 31, "cases_reported": 310, "hours_reported": 50.0}
    c = adjusted["comparison"]
    assert c["baseline_hours_per_month"] == round(50 * 30.44 / 31, 3)
    assert c["baseline_minutes_per_case"] == round(50 * 60 / 310, 3)
    assert c["after_minutes_per_case"] == 4
    assert c["minutes_per_case_change"] == round(50 * 60 / 310 - 4, 3)
    assert c["modeled_hours_per_month"] == 25
    assert result["comparison"]["observed_hours_per_month_change"] != c["observed_hours_per_month_change"]
    assert adjusted["measurement_sources_independently_verified"] is False
    assert adjusted["causal_effect_verified"] is False
    assert adjusted["realized_savings_verified"] is False
    assert result["measurements"][-1]["source"] == "system_report"


def test_overlap_is_refused_and_deleting_conflicting_measurement_restores_view():
    ident = implemented()
    today = date.today()
    first = benefits.record(ident, entry("baseline", today - timedelta(days=70), 20, 100, minutes=8))
    benefits.record(ident, entry("baseline", today - timedelta(days=55), 10, 70, minutes=7))
    result = benefits.record(ident, entry("after", today, 1, 5, minutes=6))
    adjusted = result["coverage_adjusted"]
    assert adjusted["status"] == "overlapping_periods"
    assert len(adjusted["overlap_pairs"]) == 1
    assert adjusted["overlap_pairs"][0]["period"] == "baseline"
    assert adjusted["comparison"] is None
    assert result["comparison"] is not None  # Backward compatibility
    repaired = benefits.delete(ident, first["measurements"][0]["measurement_id"])
    assert repaired["coverage_adjusted"]["status"] == "ready"


def test_zero_case_period_blocks_case_exposure_comparison():
    ident = implemented()
    today = date.today()
    benefits.record(ident, entry("baseline", today - timedelta(days=50), 30, 100, minutes=6))
    result = benefits.record(ident, entry("after", today, 1, 0, hours=3))
    assert result["coverage_adjusted"]["status"] == "zero_case_period"
    assert result["coverage_adjusted"]["comparison"] is None


def test_missing_periods_and_inclusive_overlap_boundary():
    ident = pipeline.create({"title": "Incomplete"})["idea_id"]
    assert benefits.report(ident)["coverage_adjusted"]["status"] == "missing_periods"
    other = implemented()
    today = date.today()
    benefits.record(other, entry("baseline", today - timedelta(days=70), 5, 5, minutes=4))
    benefits.record(other, entry("baseline", today - timedelta(days=65), 5, 5, minutes=4))
    assert benefits.record(other, entry("after", today, 1, 1, minutes=4))["coverage_adjusted"]["status"] == "ready"
    # Inclusive end day shared by two rows must count as a conflict.
    result = benefits.record(other, entry("baseline", today - timedelta(days=66), 2, 3, minutes=4))
    assert result["coverage_adjusted"]["status"] == "overlapping_periods"
    assert len(result["coverage_adjusted"]["overlap_pairs"]) == 2
