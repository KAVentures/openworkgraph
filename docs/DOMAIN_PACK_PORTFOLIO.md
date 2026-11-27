# Multi-log domain pack portfolios (R23)

Compare an installed domain pack on a baseline log against two to seven
additional retained logs. This supports local comparisons of operational
units, datasets, or observation periods with explicit source mappings.

## Workflow

1. Install the same pack version with matching parameters on each log.
2. On the baseline log, open **Compare a portfolio of installed packs**.
3. Load baseline packs and select the pack and comparison logs.
4. Review the source IDs, event/object counts, observation periods,
   parameters, and canonical role-to-source mappings.
5. Confirm the role meanings and population scope, then compare the
   reviewed portfolio. Download JSON or CSV from the report.

Every selected source is evaluated once. Metrics, case-rule outcomes,
object-rule results and reference lifecycle counts align by canonical
pack definition, not local activity or object names. A comparison delta
always means comparison minus the explicit baseline; it is not a
ranking or a comparison between two non-baseline sources.

The report retains each source's denominators, coverage and unknown
counts. Unmapped definitions, empty activation populations and incomplete
computations remain unavailable rather than becoming zero. Case-rule
rates use decided activated cases, with change bounds accounting for
unknown activated cases. No aggregate sums or pooled population estimates
are produced: different logs can contain overlapping cases.

Equal retained-content fingerprints are flagged even when log IDs differ.
Different fingerprints do not imply disjoint datasets. Source periods,
selection processes and source completeness can differ.

## API

Dashboard-session-only POST endpoints:

- /v1/event-logs/{log_id}/domain-packs/{pack_id}/portfolio/preview
- /v1/event-logs/{log_id}/domain-packs/{pack_id}/portfolio

Preview accepts `{"other_log_ids":["log_a","log_b"]}`.
Comparison accepts the same selection plus
`expected_preflight_sha256` from the reviewed preview.

The comparison rechecks the exact selected installations, retained
event/object/link fingerprints, catalog definition and source metadata
under the workspace lock before computing results. It rejects changed
sources or configurations rather than using a stale reviewed basis.

The total work budget is 750,000 event, object and event-object-link
rows across at most eight sources, and the original pairwise benchmark's
definition limits and exclusions apply. Both benchmarks share the same
nonblocking execution guard. No sample is silently substituted when the
budget is exceeded.

The report is descriptive only, with no business savings, causal,
legal-compliance, execution or source-completeness claims. Source
fingerprints do not cover supplied object relationships or temporal
object history. Local mapped labels and source names may be sensitive,
even though the exported matrices contain no raw case evidence.

## Regression coverage

Tests compare every matrix cell and baseline delta against the existing
pairwise engine, verify one report computation per source, detect
identical content, exercise unmapped optional roles, reject incompatible
parameters and stale source/installation pins, enforce input and work
limits, and cover dashboard-only HTTP access. A Chromium scenario imports
and configures three actual OCEL logs and exercises the portfolio UI.
Real-customer acceptance and enterprise-scale performance remain
separate verification tasks.
