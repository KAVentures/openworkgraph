# Enterprise publication quality review and gated mining export

Part of enterprise delivery #1572 and R3/R6/R20. Follow-up to consolidated
integration #1589. This is a separate, optional quality-review workflow over
already approved, unexpired connector event publications. It does not read
providers, modify source checkpoints, require additional event retention, or
make the original export path subject to a new mandatory policy.

## Review and export

1. Open Gateway **Connector event integration → Publication evidence quality**
   as an owner. Select one to ten exact published snapshots. A snapshot is
   pinned by job, grant, manifest digest and publication revision.
2. Enter at least one default quality gate. Choose a replacement policy for
   an individual source if its evidence semantics differ. An override replaces
   the complete default policy; it is not an implicit partial merge.
3. Approve aggregate inspection. Review each source's diagnostics, applied
   policy and pass/fail/indeterminate outcomes. A report contains counts,
   policies, window/checkpoint and publication provenance, not activity
   labels, event identities, case identities or source credentials.
4. Save the JSON report if desired. All selected sources must pass before
   a quality-gated export is offered. Separately approve the reviewed gates
   and event disclosure. The server rechecks the exact grants and complete
   quality digest inside the same organization transaction as export.
5. Export JSON with embedded quality provenance, or machine-import CSV
   with the separate exact quality report. The CSV response includes an
   X-OWG-Quality-SHA256 header. Preserve the quality report and mapping
   alongside the CSV. Do not open machine CSV in a spreadsheet.

For a linked export, first download the passing quality report for the same
source publication scope, then create and preview the linkage in **Reviewed
cross-source business cases**. Import the report into its optional gated
export fieldset. The server validates exact scope equality, both review
digests, both sets of approvals and current grants in one organization
transaction. The JSON contains source-quality provenance and the ordinary
reviewed linkage mapping, including its review-specific case namespace.
It does not establish that the linked cases are actually the same business
entity. Use a new local mining log for changed linkage plans.

## Metric and policy semantics

The sample is bounded by the disclosed snapshot, not the full business
process. Each source is evaluated independently; a large source cannot
hide a failure in a smaller one. The report's total case count is the sum
of source-local cases and not a count of distinct enterprise cases.

Supported gates:

| Gate | Comparison |
|---|---|
| Minimum event count | At least threshold |
| Minimum source-local case count | At least threshold |
| Maximum singleton case rate | At most threshold |
| Maximum single-activity case rate | At most threshold |
| Maximum largest activity share | At most threshold |
| Maximum reported timestamp-tie event rate | At most threshold |
| Minimum cases eligible for reported order | At least threshold |

Rate thresholds use integer basis points in the API (0–10000); the UI uses
percentages. Positive fractional rates round upward to the next basis point
for maximum-rate checks. No implicit default quality standards are imposed.
A failing or indeterminate gate blocks the optional gated export. A new
policy requires a new report and explicit review.

Additional diagnostics include events-per-case extrema and nearest-rank
p50/p90, repeated activity occurrences within cases (not necessarily
rework), concentration, same-reported-time buckets and cases, reported
activity-sequence diversity, and reported span quantiles. Sequences and
spans are computed only over cases with distinct reported timestamps and
not identified as SAP business-date evidence. Ties are not broken using
an arbitrary event ID. The report does not verify physical action times,
actual waits, process completeness, business outcomes or causal order.
Singletons, repeated labels and concentrated activities may be legitimate.

SAP canonical midnight labels are reported business dates, not action
timestamps. The timestamp-tie gate is indeterminate for this provider.
Same-reported-date counts remain visible, but sequence and span statistics
are suppressed. An operator can select an explicit source override
appropriate to business-date evidence. Other providers may also have
limited timestamp precision or visibility; absence of the SAP flag does
not prove precise occurrence times.

## API and transaction contracts

Owner-only POST routes under /v1/admin/connector-events/{organization_id}:

- quality-review: snapshots, policy, source_overrides, confirm_inspection.
- quality-export / quality-export-csv: same review request plus
  expected_quality_sha256, confirm_quality_gated_export and
  confirm_event_export.
- case-quality-export: ordinary exact linked export request plus nested
  quality review, expected_quality_sha256 and confirm_quality_gated_export.

All routes validate exact live publications and row digests under the
existing organization transaction. Combined event budget remains 20,000
and per-publication budget remains 5,000. The canonical quality digest
covers the exact publication pins, expiry, applied policies, all aggregate
diagnostics and outcomes. Input publication order does not change it.
Revocation, expiry, changed grant, policy or derived report invalidates
approval. Failed and indeterminate reviews are downloadable as counts-only
diagnostics but cannot authorize a gated event export. Audit receipts
contain counts and digests, not event rows. No policy or report is
persisted as a new Gateway data store. Previously downloaded files are
not recalled by later grant expiry.

## Validation

The exact committed pure module (Git blob
f6d3d04ce2e0b600a69736d82cc07fca0111eec9) was verified locally using
3,000 randomized independent grouping checks, input reversal invariance,
date precision and threshold boundaries. A synthetic 20,000-row aggregation
completed in approximately 0.01 seconds in the authoring environment;
this is not a full pipeline benchmark.

Regression fixtures use the existing actual SQLite/PostgreSQL publication
store, linked review and retained mining import/replay. They exercise
corruption, stale or revoked publications, different organizations, owner
authorization, explicit gates, source overrides, and both quality and
linkage digest fencing. Chromium fixtures exercise failed/passing gates,
consent invalidation, JSON/CSV download and passing-report handoff into
linked export. Focused draft acceptance is registered alongside the
consolidated enterprise suite.

Direct local repository checkout is unavailable due to GitHub DNS, so
the integrated fixtures and browser flows have not been executed locally.
CI, live customer acceptance, and the parent stack's required checks
remain outstanding. This workflow does not impose an enterprise-wide
mandatory policy, independently measure source completeness, certify
regulatory compliance or cover all possible source timestamp semantics.
