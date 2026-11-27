# R21 — Differential decision-domain review

Open a saved process revision, load revision history, compare an earlier
revision with the current opened revision, and expand **Compare synthetic
decision domains across revisions**. This feature compares exact finite
behavior without requiring users to write an expectation for every case.

## Declare the domain

Enter one to eight distinct calendar dates and one to eight dimensions using
the existing finite decision-domain format. A dimension contains an exact
JSON pointer and up to 24 choices, each a scalar or explicit missing marker.
The product may contain at most 2,048 combinations per date and 4,096 total
dated points. Both saved model revisions see every identical synthetic
input/date. Missing, null and boolean values remain distinct. The normal
decision dependency evaluator runs independently for each model.

Load editable boundary examples from both revisions if helpful. Review
overlapping paths, omitted paths, duplicate-equivalent choices and the
combined size before running. The suggestions are not an exhaustive
partition. No real customer facts should be supplied.

## Behavior analysis

Align decisions by stable table ID. Added/removed tables or a changed
decision-activity identity are explicitly marked identity-discontinuous.
Report exact counts of exclusive primary categories (identity change,
selection lost, selection gained, target changed, status changed,
diagnostic change, unchanged) and separate nonexclusive flags. For example,
a target change can simultaneously change diagnostic rows. Compare selected
row identities, row match/unknown status, blocking row and blocking table,
even when the selected target remains unchanged. Dates evaluate effective
and expiry boundaries without silently carrying a selection forward.

Per-date and per-table summaries, exact status and target transitions,
row diagnostic transitions and paged input-point witnesses are available.
A witness uses zero-based indices into the original dimensions, with dates
sorted chronologically. Input values are not copied into the report.
A row diagnostic absent on one side is not_evaluated_or_absent, not false.
The report concerns evaluator outputs; rule text, business meaning and
model structure still require separate review.

## Compact witness selection

For each changed table/date, define an obligation for the observed outcome
transition (status, target, selected/blocking identities and primary category).
Also define one obligation for each changed row diagnostic transition.
A point may satisfy several obligations across tables. Deterministic greedy
selection chooses the point covering the most uncovered obligations, ties
broken by original point order. The operator chooses a 1–50 point budget.
The report gives exact total, covered and uncovered obligations, a lower
bound ceil(total / largest single-point coverage), and marks cardinality
optimal only if a complete cover matches that bound. Otherwise no minimum
is claimed. No more than 20,000 distinct obligations are accepted.

This covers distinct structural transition signatures, not all changed
input points. Witnesses are synthetic test candidates, not independently
reviewed expectations. Use the existing reviewed regression suite separately
to decide whether each change is intended. Selection gained/lost is not
automatically improvement/regression.

## API and privacy

Dashboard-session-only POST, no-store:

    /v1/process-catalog/decision-domain/compare?workflow_id=...&before_revision=1&after_revision=2&before_sha256=...&after_sha256=...

Both exact saved references are checked before and after the evaluation.
The request has dates, dimensions, optional witness_limit (1–50), filter,
table_id, offset, limit (1–50) and expected_review_sha256. Later pages must
include the pin from the first report. The pin binds both saved references,
sorted dates, exact choices and algorithm format. It is not a signature.
Filters: all, changed, selection_lost, selection_gained, target_changed,
status_changed, diagnostics_changed, identity_changed, unchanged.
Filtering affects evidence pages, not full-domain counts or witness cover.
Requests are bounded to 64 KiB; strict duplicate-key parser applies. One
local comparison at a time; oversized domains/obligation budgets refuse
rather than sample. Results are not persisted; explicit JSON download
contains structural IDs, exact digests and choice indices. Retain the
synthetic input separately to reproduce the witnesses. Changing domain
inputs or revision selection invalidates the displayed report.

No arbitrary-domain equivalence proof, approved policy, observed case
distribution, independent human expectation, deployment or business
execution is implied. No source systems or providers are contacted.

## Verification

New regressions cover exact paired changes, temporal expiry, missing
tables, FIRST diagnostic-only changes, dependency propagation, paging and
minimization, strict input refusal, guard recovery and 120 independent
small exhaustive set-cover oracles. Existing authenticated local HTTP
and real browser revision-comparison tests are extended. Native CI
must verify the exact PR head.
