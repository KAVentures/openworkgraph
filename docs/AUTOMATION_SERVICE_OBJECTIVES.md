# Reported automation service objectives (R18)

In Automation monitoring, select a retained deployment and choose **Service
objectives**. Specify a start cohort, exclusive end, observation cutoff,
deadline in seconds and target in basis points (9500 = 95%). Optionally
require the automation's reported verified-outcome flag or an exact
reported specification digest.

This is a retrospective analysis of the latest retained record per run,
not a reconstructed historical view of what OWG knew at the observation
cutoff. Report arrival times and overwritten revisions are not recovered.
No external monitoring or production traffic completeness is inferred.

## Classification and error budget

Every retained run started in the half-open cohort [start, end) enters the
denominator. A run meets the objective only if it reports success within
the deadline and before or at the cutoff. If the outcome requirement is
enabled, it must also report a verified outcome. A false flag misses the
objective; a missing flag is unknown. Neither flag independently verifies
a business outcome.

A finished failure, handoff, cancellation, or excessive duration misses.
A still-running run or recorded finish after cutoff misses if the deadline
has already elapsed, otherwise remains pending. A terminal report without
a finish timestamp is unknown. Exact-deadline success is accepted. With
an exact specification requirement, missing or different run attribution
is unknown rather than silently excluded.

For N retained starts and a target of T basis points, the required number
of successful runs is ceil(N*T/10000); the permitted misses are N minus
that value. Lower attainment is met/N; upper attainment is
(met+pending+unknown)/N. The target can be met even in the worst case,
missed even in the best case, or unresolved. No runs means no verdict.
The remaining error budget is shown after known misses and assuming
all unresolved runs miss. Negative values explicitly represent an
overrun. These are exact retained-count bounds, not confidence intervals.

Daily cohorts are 24-hour UTC intervals from the selected start; the last
may be partial. Empty buckets remain visible. All use the same cutoff,
target and deadline, and therefore have different follow-up lengths.

## Evidence and API

The objective panel filters and pages retained run evidence without
changing the denominator. Each page is pinned to the same population
digest covering the objective settings, deployment pins and ordered
retained run records. Replaced or added cohort records invalidate later
pages; start again. Download exports the aggregate and current evidence
page, not all matching runs. No run status is changed.

POST /v1/automation-monitoring/deployments/{deployment_id}/service-objectives
requires a local dashboard session and strict JSON (16 KiB). Example:

```json
{"start":"2026-11-01T00:00:00Z","end":"2026-11-08T00:00:00Z",
 "as_of":"2026-11-09T00:00:00Z","deadline_seconds":3600,
 "target_basis_points":9500,"require_reported_verified_outcome":true,
 "status":"missed","limit":30}
```

Optional exact_spec_sha256 limits certainty to exact run attribution.
Use next_offset and population_sha256 as offset and
expected_population_sha256 for another page. At most 90 days, 200,000
retained cohort runs and 100 rows per page. Exceeding the work limit
refuses the review; it does not silently sample. No persistent objective
configuration, scheduling, alerting, provider calls, execution permission,
or verified SLA claim is introduced. Regression fixtures cover exact
integer budgets, cutoff semantics, unresolved evidence, stale cohort
pins and authentication.
