"""Human-declared before/after outcome measurements, never verified realized savings.

Only bounded aggregate numbers and SHA-256 receipt references cross into the
organization process catalog. The references are *not* proof that OWG inspected
or authenticated their underlying business-system records.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator

from .process_hierarchy import Digest, Hierarchy, Reference, Strict

Money = Annotated[str, Field(pattern=r"^(0|[1-9][0-9]{0,11})(\.[0-9]{1,2})?$")]
Currency = Literal["USD", "EUR", "GBP", "SEK"]
MAX_REVISIONS = 20


class Period(Strict):
    started_at: datetime
    ended_at: datetime
    case_count: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    total_handling_minutes: Money
    receipt_sha256: list[Digest] = Field(min_length=1, max_length=20)
    coverage: Literal["complete", "partial", "unknown"]
    provenance: Literal["user_supplied_aggregates_unverified"] = "user_supplied_aggregates_unverified"

    @model_validator(mode="after")
    def boundaries(self):
        for point in (self.started_at, self.ended_at):
            if point.tzinfo is None or point.utcoffset() is None:
                raise ValueError("Period timestamps must have explicit time zones")
        if self.started_at >= self.ended_at:
            raise ValueError("Period must have a positive duration")
        if len(set(self.receipt_sha256)) != len(self.receipt_sha256):
            raise ValueError("Duplicate receipt fingerprints within one period")
        return self


class Measurement(Strict):
    format: Literal["openworkgraph.process-value-measurement.v1"] = "openworkgraph.process-value-measurement.v1"
    organization_id: Annotated[str, Field(min_length=1, max_length=512)]
    root_process: Reference
    title: Annotated[str, Field(min_length=1, max_length=120)]
    currency: Currency
    baseline: Period
    intervention: Period
    assumed_hourly_cost_minor: Annotated[StrictInt, Field(ge=0, le=100_000_000)]
    periods_and_case_cohorts_reviewed: StrictBool = False
    independent_source_receipts_verified: Literal[False] = False
    realized_savings_verified: Literal[False] = False
    causal_effect_established: Literal[False] = False
    execution_authorized: Literal[False] = False

    @model_validator(mode="after")
    def paired_periods(self):
        self.title = Hierarchy.plain_text(self.title)
        self.organization_id = Hierarchy.plain_text(self.organization_id)
        if not self.title or not self.organization_id:
            raise ValueError("Title and organization required")
        if self.baseline.ended_at > self.intervention.started_at:
            raise ValueError("Baseline and intervention periods must not overlap")
        if set(self.baseline.receipt_sha256) & set(self.intervention.receipt_sha256):
            raise ValueError("The same supplied receipt cannot support both periods")
        return self


class Saved(Strict):
    format: Literal["openworkgraph.saved-process-value-measurement.v1"] = "openworkgraph.saved-process-value-measurement.v1"
    measurement: Measurement
    author_actor_id: str
    saved_at: str


class Save(Strict):
    measurement: Measurement
    expected_revision: Annotated[StrictInt, Field(ge=0, le=MAX_REVISIONS)] = 0
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False


class Remove(Strict):
    expected_revision: Annotated[StrictInt, Field(ge=1, le=MAX_REVISIONS)]
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False


def compare(measurement: Measurement) -> dict:
    """Compare case-normalized means, without mistaking associations for impact.

    Without distribution/case-mix controls, no confidence intervals, causal
    savings, source authentication or independent business-outcome claims exist.
    """
    b, i = measurement.baseline, measurement.intervention
    missing = []
    if not measurement.periods_and_case_cohorts_reviewed:
        missing.append("case_cohort_review_required")
    if b.coverage != "complete" or i.coverage != "complete":
        missing.append("complete_period_coverage_required")
    if b.case_count < 30 or i.case_count < 30:
        missing.append("at_least_30_cases_per_period_for_descriptive_comparison")
    result = {
        "status": "withheld" if missing else "descriptive_comparison_only",
        "blockers": missing, "comparison": None,
        "independent_source_receipts_verified": False,
        "realized_savings_verified": False,
        "causal_effect_established": False,
        "case_mix_adjusted": False, "statistical_confidence_interval_available": False,
        "execution_authorized": False,
        "interpretation": "Human-supplied aggregates; receipt hashes are references, not authenticated provider evidence. Differences may reflect case mix, seasonality or selection bias.",
    }
    if missing:
        return result
    before = Decimal(b.total_handling_minutes) / Decimal(b.case_count)
    after = Decimal(i.total_handling_minutes) / Decimal(i.case_count)
    delta = before - after
    def fmt(value):
        return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")
    result["comparison"] = {
        "baseline_cases": b.case_count,
        "intervention_cases": i.case_count,
        "baseline_mean_handling_minutes_per_case": fmt(before),
        "intervention_mean_handling_minutes_per_case": fmt(after),
        "descriptive_minutes_difference_per_case": fmt(delta),
        "assumed_labor_value_difference_per_100_cases_minor": fmt(
            delta * Decimal(measurement.assumed_hourly_cost_minor) * Decimal(100) / Decimal(60)
        ),
        "currency": measurement.currency,
        "value_type": "assumed_labor_cost_delta_not_realized_savings",
        "numbers_are_decimal_strings": True,
    }
    return result
