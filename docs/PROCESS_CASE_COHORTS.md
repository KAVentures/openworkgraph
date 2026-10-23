# Analyze complete case cohorts

Open or explicitly save a local event-log snapshot in the Evidence tab. Under
**Filter complete retained cases**, optionally supply:

- **Case first event from**: an inclusive offset-aware ISO timestamp.
- **Case first event before**: an exclusive offset-aware ISO timestamp.
- **Case contains exact activity**: a literal activity label present anywhere
  in the case, including after the diagram's display limit.
- **Retained variant ID**: an exact `variant_N` identifier from that snapshot.

Select **Apply case filters**. Input changes alone do not read source systems,
recompute a report or retain anything. **Clear case filters** reopens the full
retained log. Filtering requires the same authenticated local dashboard session
as retained-log browsing; ordinary AI bearer credentials cannot access it.

Date selection is based on the first retained event in each case. Bounds are
normalized to UTC and select whole cases; every event of each selected case
remains in the analysis, even events outside the selected date window. There is
no event clipping that could manufacture a shorter path, a false terminal or an
apparent conformance violation. First/last logged events do not establish actual
business start/completion. This selection is not a source-system completeness
claim or a new cross-system/cross-person identity join.

Maps, variant counts, activity/transition incidence, elapsed durations, repeated
activity counts and optional exact-revision structural comparison are computed
from all selected cases and their full paths. The usual bounded display samples
remain explicit. Original snapshot case ordinals and variant IDs are preserved,
including when frequency ranking reverses or selected examples were outside the
original 30-case preview. Variant selectors display those retained IDs, rather
than renumbering the first displayed cohort route as variant 1.

The case browser, next-page controls and selected-activity drill-down retain the
active cohort filters. A clicked activity is an additional AND condition, so
drill-down cannot silently broaden the selected population. Event pages expose
the entire selected case chronology under its original ordinal. Empty selections
return explicit zero case/event counts and no report, rather than zero-valued
timing statistics or stale results. Changing the source log or draft filter
inputs invalidates pending responses. Saving a proposed design remains a separate
reviewed action; filtering never updates process models or execution rights.

Timestamp ties and repeated-event diagnostics are recomputed for selected cases.
The original input's nonchronological ordering cannot be recovered per selected
case after retention, so that selected-population count is `null`. The original
whole-snapshot diagnostics appear separately as `original_snapshot_data_quality`.
They are not mislabeled as diagnostics for the filtered population.

## API

`GET /v1/process-mining/datasets/cohort` takes `dataset_id` and optional
`started_since`, `started_before`, `contains_activity`, `variant_id`. An optional
`workflow_id` and exact `revision` together compare all selected complete paths
against that saved process revision. Business rules, conditions, outcomes,
active labor and waiting remain unverified.

The response format is `openworkgraph.process-log-cohort.v1`. It includes:

- The unchanged dataset reference and retention dates.
- `selection`: canonical filters, original/selected case counts, selected event
  count, explicit whole-case selection basis and preserved identity flags.
- `report`: the regular mining projection with additional `cohort` scope, or
  `null` for a valid empty selection.
- `selection_retained: false`.

`GET /v1/process-mining/datasets/cases` accepts the same cohort filters along
with its existing `after`, `limit`, and additional exact `activity` drill-down.
All SQL values are parameterized. Activity filters match literal labels rather
than executing expressions. Naive dates and invalid/inverted windows are
rejected. Both metadata and full event selection use one SQLite read snapshot,
so concurrent deletion cannot produce a partial cohort. Expired/deleted logs
remain unavailable. Successful responses use `Cache-Control: no-store`.

No new tables, native identifiers, source values, model-provider requests,
business writes or retention controls are introduced. Existing per-log input
bounds remain 1 MiB, 5,000 events, 1,000 cases and 64 activity labels. These
cohorts operate on explicitly retained local snapshots; organization-wide source
cohorts need their own identity and access-control integration. Explicitly
minimized participant/team labels are supported within one saved import; see
[participant/team cohorts](PROCESS_PARTICIPANT_COHORTS.md). Native identities and
verified cross-person handoffs are not established.

Validation covers UTC conversion and inclusive/exclusive boundaries, full events
outside the date window, hidden activities, immutable case/variant identity,
rank reversals, paging, independent additional activity filters, full-path
structural comparison, empty selections, validation/authentication, no writes,
browser request invalidation and the actual dashboard/API/SQLite flow.

[Compare two complete-case populations](PROCESS_COHORT_COMPARISON.md) using the
same filters and saved snapshot, with full histories and explicit intersection
counts. Comparison never modifies the saved log or a process model.

[Transition drill-down](OBSERVED_TRANSITION_INSPECTION.md) adds an exact adjacent
activity-pair condition to case browsing. Its case and event pagination retain
the active cohort filters and include each matching case only once.

[Reviewed shared-case imports](REVIEWED_SHARED_CASE_IMPORTS.md) retain scoped
case-key and event-origin aliases. Whole-case reconstructions preserve their
original values, including a one-origin cohort from a multiple-origin snapshot.
Native keys, source identity and cross-import equivalence are not recovered.
