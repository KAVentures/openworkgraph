# R16 — Intervention case mix and common-support review

Open a saved event log and its Intervention impact section, then expand
**Case mix and common-support intervention review**. Select the current case
projection, UTC intervention date, metric, optional equal windows, recent
exclusions and spanning-case handling. Optionally enter the existing
treated/control case-filter JSON. Choose first recorded activity, first
recorded resource, or UTC start weekday (ISO 1=Monday, 7=Sunday), and
minimum support (5–10,000 cases per cohort/period). Prepare and inspect the
exact request, then run. This review is separate from the bootstrap and
time-profile reports.

## Population and labels

Uses exactly the existing projected case metric and start-based period
assignment. Explicit treated/control groups must not overlap. Cases outside
both groups and cases excluded by the period rules are separately counted.
First-event activity/resource is assigned only when every tied event at the
earliest timestamp has the same nonempty value. Missing and ambiguous
start labels remain separate strata in raw counts but are not eligible for
standardization. The UTC weekday is derived from case start. No arbitrary
tie-breaking or missing-label imputation. First recorded is not guaranteed
to be the first actual event or a pre-treatment variable.

Every known stratum must meet the chosen minimum case count in every
selected cohort and period to enter common support. With a control, the
same support is used across all four cells. Means in any under-supported
stratum/cell are withheld; exact counts remain visible to the authorized
local dashboard user. Counts and names are not an anonymity guarantee.

## Exact decomposition

For a group, restrict to common support. Let w0(s),w1(s) denote its
baseline/follow-up case shares, and m0(s),m1(s) their metric means. Its
common-support change is exactly the sum of:

    within = sum_s (w0(s)+w1(s))/2 * (m1(s)-m0(s))
    composition = sum_s (m0(s)+m1(s))/2 * (w1(s)-w0(s))

The engine uses rational sums of retained metric values and verifies the
identity before display rounding. The raw full-population change minus
the common-support change is a separately disclosed residual; do not
assign it to within-stratum or composition effects.

For standardized means, each common stratum gets the arithmetic mean
of its cell-specific common-support shares (equal weight per cohort/period
cell, not pooled volume). The same reference shares apply to every cell.
With a control, report raw, common-support and standardized descriptive
difference-in-differences and differences of decomposition components.
No unsupported cell is imputed; no common support means no standardized
estimate. No extra confidence interval or causal identification assumption
is asserted.

## Review and evidence

Reports include exact counts, coverage, per-stratum support and mean,
reference weights, and group decomposition. Each stratum/group/period
can be reviewed in exact case pages, pinned to a SHA-256 digest of selected
source rows, period assignment, start labels and settings. If any relevant
basis changes, the next evidence request is refused; rerun the review.
The digest is not a signature or proof of source completeness. Explicit
JSON exports are point-in-time reports or evidence pages. No new retained
store, remote action or change to the original intervention analysis.

Dashboard-session-only POST:

    /v1/event-logs/{log_id}/impact/case-mix

    {"analysis":{"intervention_at":"2024-03-01T00:00:00Z",
                 "metric":"cycle_time","case_type":"case"},
     "case_mix":{"dimension":"first_activity","minimum_cases":10}}

The evidence endpoint appends /evidence and requires the same analysis
and case_mix objects, expected_review_sha256, stratum_sha256, group,
period, and optional after and limit (1–50). Use the actual projection
from the retained log. Strict JSON request limit 32 KiB. At most 200,000
projected cases and 100 observed included strata. Oversized reviews
refuse rather than sample; one local review at a time.

## Limitations

This separates descriptive changes in the recorded population mix from
changes within known start strata. It does not adjust unmeasured
confounders, establish parallel trends, verify actual causal effect,
estimate work effort, or claim realized savings. If the intervention
changes the start activity/resource itself, conditioning on that label
can distort interpretation. Choose the dimension deliberately.

## Verification

The new regressions cover 160 independent randomized rational decomposition and standardization oracles, composition-only changes, unsupported and ambiguous strata, retained case membership, stale evidence, original-period accounting, bounded scans, authenticated API and Chromium source-review/export workflows. Full native CI remains required.
