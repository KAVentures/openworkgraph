# Compare complete case populations

Open a saved local log in **Evidence → Process mining · event logs**. Under
**Filter complete retained cases**, expand **Compare two complete-case
selections**. Set optional date, activity, variant, participant or team filters
and choose **Keep filters as comparison baseline**. Empty filters select the
whole saved log. Change the inputs for the current population, review both
selections, check the comparison confirmation and choose **Compare case
selections**. Keeping filters and editing inputs make no analysis request.

The baseline remains frozen while current inputs change. Neither selection
needs to be applied to the explorer first. The response lists the exact
canonical filters used for each population. Input changes, another baseline,
another source, withdrawing review or changing the exact reference revision
clear previous results and invalidate pending responses. A comparison waiting
for dashboard authentication is canceled before sending if review is withdrawn.
Baseline filters and comparison results remain in browser memory only.

Every event of each selected case contributes, including events outside date
filters or performed under other supplied participant/team labels. Selecting
both participant and team requires their occurrence on the same event, as in
the existing whole-case explorer. Date bounds select cases by their first
retained event; they do not clip event history. Each population must contain
at least one case; empty selections return a validation message without
fabricated zero medians or distributions.

The existing full-history comparison engine supplies path case shares,
within-path median elapsed time, activity event and distinct-case counts,
transition support and elapsed gaps, overall median/p90 durations and path-mix
total variation distance. All cases and complete paths contribute, including
cases beyond preview examples and paths beyond the display cap. The existing
path/transition display bounds remain explicit. An optional selected process
revision compares both populations with the same immutable structural model;
no business rules, conditions or outcomes are evaluated.

Both populations come from one immutable retained snapshot, so unchanged case
ordinals establish exact intersection counts: shared cases, baseline-only,
current-only and their union. No native identifier is recovered and no identity
is joined across imports. Overlapping populations are permitted and reported;
they are not presented as independent samples. Disjoint selections also do not
establish statistical independence or comparability of business complexity.
The comparison is descriptive, without significance tests, causal conclusions,
employee performance rankings, verified labor, waiting or savings. Elapsed
log times remain distinct from those measures. Timestamp ties have unverified
order; per-case ties are recomputed from selected full histories. Original
input row ordering was discarded at retention and is not reconstructed.

**Inspect baseline case selection** and **Inspect current case selection**
reopen that population in the same map, variant inspector and full case/event
browser. The usual unsaved-design confirmation still applies. Comparing never
changes a saved process, review, execution permission, source system or log.

## API

`POST /v1/process-mining/datasets/cohorts/compare` requires a current local
dashboard session. Ordinary AI bearer tokens cannot read retained populations.
The JSON body is bounded to 4 KiB, rejects duplicate keys/unsupported fields,
and uses this shape:

```json
{
  "dataset_id": "log_0123456789abcdef0123456789abcdef",
  "baseline_filters": {},
  "current_filters": {"contains_team": "team_2"},
  "comparability_confirmed": true
}
```

Each filter object accepts `started_since`, `started_before`,
`contains_activity`, `variant_id`, `contains_participant`, `contains_team`.
Optional `workflow_id` and integer `revision` must appear together. Review is a
strict boolean, separate from log retention and source-label analysis consent.
Validation errors do not echo supplied values. Dates and aliases use the
existing cohort validators; SQL values are parameterized.

Responses use `openworkgraph.process-cohort-comparison.v1`, reuse the existing
comparison tables and add `comparison_scope` and `cohort_overlap`. Each reference
includes the dataset, its original case count, selected counts, canonical
filters, expiry, event window and SHA256 of selected ordered structural rows.
The structural digest excludes participant/team identity values; filters and
snapshot identity define population scope. Coverage explicitly describes all
selected complete cases, rather than claiming the entire dataset was selected.
Both populations' metadata and complete rows are acquired in one SQLite read
transaction, so concurrent deletion cannot produce partial histories. Deleted
or expired snapshots are unavailable to new requests. Responses use
`Cache-Control: no-store`; no new tables or retained comparison are created.

Validation covers hidden long paths and cases beyond preview limits, exact
case intersections, disjoint/reversed populations, full histories beyond date
bounds, zero durations, tied timestamps, exact model comparison, deletion
concurrency, expiry, bounded/authenticated API requests, private error values,
pending-review invalidation and a production dashboard/API/SQLite browser flow.
