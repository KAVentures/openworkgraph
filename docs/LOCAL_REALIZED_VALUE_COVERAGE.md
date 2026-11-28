# Coverage-aware realized-value comparison (R16)

OWG's local automation pipeline already records reviewer-entered baseline and
post-implementation effort measurements. The original report averages
**per-period normalized rates equally**, which gives a one-day period as much
weight as a thirty-day period. It is retained unchanged for compatibility.

A new **Coverage-aware comparison** appears alongside the legacy figures in
the dashboard. The existing authenticated endpoint
`GET /v1/automation-pipeline/{idea_id}/realized-value` adds the
`coverage_adjusted` object. No new endpoint, export permission or external
provider access is required.

## Data-quality statuses

- `ready`: at least one baseline and after period, no overlapping dates
  **within** either kind, and every entered period reports a nonzero
  number of cases.
- `missing_periods`: baseline or after is missing.
- `overlapping_periods`: intervals of one kind share at least one calendar
  date. `overlap_pairs` names the conflicting measurement IDs. The adjusted
  comparison is withheld until the duplicate coverage is removed.
- `zero_case_period`: at least one entered period reports zero cases,
  preventing an interpretable per-case denominator.

Dates are inclusive. Two adjacent intervals with the first ending on the day
**before** the next starts are disjoint; a shared date is an overlap.
The comparison never silently double-counts days. Historical legacy
calculations are not changed and may still be displayed, explicitly labeled
as equal-weight-per-period results.

## Duration-weighted arithmetic

For each non-overlapping cohort, pool the total entered hours, total cases and
calendar days. Then calculate:

- Hours/month = total hours / total reported days × 30.44
- Cases/month = total cases / total reported days × 30.44
- Minutes/case = total hours × 60 / total cases
- Change in hours/month at after volume = (baseline minutes/case minus
  after minutes/case) × after cases/month / 60

Raw values are pooled before rounding for display. Negative changes are
preserved. Coverage gaps between reported periods are not interpreted as
observed zero activity. These calculations are a *descriptive alternative*,
not a matched control study or a forecast.

## Verification and authority boundaries

`source=system_report`, `source=event_log` and other labels are all
**reviewer assertions**. This feature does not fetch or verify raw reports,
case membership, measurement identity or external receipts. Its response
explicitly states `measurement_sources_independently_verified=false`,
`causal_effect_verified=false` and
`realized_savings_verified=false`. It does not calculate financial value or
authorize implementation, agent actions, sharing or additional capture.

Only the existing local dashboard session can read or update the report.
The comparator is computed on read and creates no new storage or background
tasks. It works with the 48-measurement-per-idea limit.

## Acceptance

`tests/test_automation_benefits_coverage.py` covers imbalanced period
lengths, pooled case/effort rates, overlapping and inclusive boundaries,
repair by deletion, zero-case refusal, missing periods and unverified
source declarations. The existing browser acceptance test
`tests/browser/automation_realized_value.cjs` also checks the new panel
and wording.


## Measurement sensitivity and case-volume effects

The coverage-aware comparison additionally refuses shared calendar dates
between baseline and after periods, including historical baselines recorded
before the idea was declared implemented. The original equal-weight report
remains available for backward compatibility and is not a substitute for
the coverage-aware comparison.

For valid nonoverlapping periods with positive case denominators, the
comparison reports an arithmetic decomposition of the baseline-minus-after
monthly hours into two components. Let e be pooled minutes per case and v
be pooled monthly case volume, with b and a denoting baseline and after.

- Effort component: (e_b - e_a) * (v_b + v_a) / 120.
- Volume component: (v_b - v_a) * (e_b + e_a) / 120.

These sum to the change in monthly hours before rounding. They average
the two possible arithmetic decomposition orders and do not identify causal
effects. The original effort-at-after-volume comparison remains unchanged.

Calendar coverage reports the inclusive span, measured days and unmeasured
days within each side, plus exposure by declared source kind. Unmeasured days
are not imputed as zero effort. Different source-kind sets are flagged for
comparability review; matching sets are not proof of consistent measurement.

The leave-one-period-out review recomputes the effort-at-after-volume
comparison after dropping each baseline or after period in turn, whenever
at least one period remains on that side. The dashboard exposes the range,
individual omitted-period scenarios and any change in direction from the
full comparison. This is a descriptive influence analysis, not a statistical
confidence interval or an estimate of realized savings. A single period on
either side does not support omission analysis on that side.

No new source reads, execution, persistence, currency estimates, or
portfolio aggregation occur. The existing coverage-aware response contains
an additive sensitivity object; invalid comparisons return null. Regression
tests cover opposing effort and volume effects, measured-date gaps, source
composition, influence-driven reversals and historical cross-period overlap.


For explicit multi-month investment alternatives, discounted cash flow and
capacity conversion sensitivity based on selected retained effort periods,
see [Phased investment business cases](PHASED_INVESTMENT_BUSINESS_CASE.md).
