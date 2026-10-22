# Local CSV process-mining preview

## Purpose

Companies may already have authoritative event logs from CRM, ticketing,
ERP, finance or another business system, even where nobody ran the OWG desktop
recorder. This first process-mining milestone provides a manual **one-time local
CSV import** independent of OWG's existing task-mining and workflow evidence.

It does not join imported data to recorded OWG activities or create a company
process model automatically. Those require explicit, verified case identities
and separate data/employee consent.

## Use

Open the **Evidence** tab, find **Process mining · CSV event logs**, select
a file, and click **Analyze selected CSV**. The workspace is headed **Process
mining · event logs** and also supports [selected GitHub/Jira source projections](GITHUB_SELECTED_CASES_PROCESS.md#shared-analysis-and-design).
No file is uploaded on page open,
on selection, or to any cloud service. The preview uses the authenticated
local OWG API and runs entirely in memory.

UTF-8 CSV schema (header names case-insensitive):

```csv
case_id,activity,timestamp,source
CASE-001,Received,2026-10-20T10:00:00Z,ticketing
CASE-002,Received,2026-10-20T10:01:00Z,ticketing
CASE-001,Validated,2026-10-20T10:03:00Z,ticketing
CASE-002,Closed,2026-10-20T10:05:00Z,ticketing
CASE-001,Closed,2026-10-20T10:07:00Z,ticketing
```

The `source` column is optional and is not returned in the preview.
The event log must already use a shared, authoritative `case_id` if
multiple source systems' events are intended to represent the same case.
OWG never infers this equivalence from names, timestamps or neighboring
activity. Timestamps must be full offset-aware ISO-8601 values.

## Results

- Counts of cases, events, activities, observed variants, and transitions.
- Activity-node and transition-edge counts.
- Separate case paths and observed path variants, without inventing merges.
- Median case elapsed time (first to last event), median inter-event gap,
  and repeated-activity count.
- A scrollable observed-path diagram, variant selectors, and transition table.

### Explore all observed transitions

The combined map has **Zoom in**, **Zoom out**, **Actual size**, and **Fit
graph** controls. Its bounded scroll region also supports keyboard panning.
Zoom changes the rendered SVG size while retaining full geometry and activity
identity; it does not change the model, counts, evidence selection or filters.

**Map emphasis** offers Uniform, Recorded frequency, or Median elapsed gap.
Frequency uses numerical occurrence counts: wider, darker arrows indicate more
recorded transitions and node shading indicates more activity occurrences.
Elapsed emphasis uses numerical median gaps in seconds with an amber scale.
Both scales are relative to the displayed map, keep all transitions visible,
and label their maximum. Measured zero and unknown are distinct; unknown values
are gray. Gap emphasis is descriptive and does not establish a bottleneck,
causal dependency, verified waiting time, labor or savings. Native tooltips and
the activity inspector retain the underlying counts and gaps.

The same view controls appear on local/shared process designs and combined
desktop candidate maps. Designs have no invented observed metrics. Desktop
candidates offer projected frequency only, with a projection-specific legend;
their normalized steps are not canonical events or verified business cases and
their step timing remains unavailable. Rendering, zoom and emphasis remain
offline and create no source reads, persistence or execution authority.

**Explore all observed transitions** draws the activity graph across every case
in the imported log. It includes forks, joins and loops using observed adjacent
pairs. Node badges show event and distinct-case counts; transition tooltips show
frequency, case counts and median elapsed gaps. Select a node with the mouse or
keyboard to inspect all its adjacent transitions and open a displayed supporting
variant. For an explicitly retained snapshot, **Browse retained cases** queries
all matching cases, including those outside the diagram/example sample.

This graph shares OWG's renderer with the process designer, with distinct
observed-data labels and limits. It supports the CSV contract's 64 activities
and 4,096 possible transition types; a bounded scroll area contains dense maps.
The designer retains its separate 40-activity/100-transition model limits.

Each arrow means recorded adjacency within a supplied case, not causality,
automatic branch policy or a required business dependency. Paths composed across
the combined graph can be absent from all real cases; exact variants remain the
source for complete observed sequences. No graph selection saves data, creates
a design, joins sources or enables an agent. Elapsed gaps remain distinct from
verified waiting and active labor.

### Inspect a variant and its supporting cases

The **Full imported log** metrics describe the whole file. Select any of the
up to 20 returned variants to scope the activity counts, transition table,
median case elapsed duration, and case examples to that exact full path.
Click an activity in the map or the activity buttons to inspect its event and
case counts and adjacent transitions. Map nodes also support Tab, Enter, and
Space. **All activities** clears the activity filter.

Each variant includes up to five case examples, independently of the older
30-case overall sample. Expand a case to see ordered activity occurrences,
source timestamps, and elapsed gaps. Repeated occurrences remain separate.
Activity counts and transition gap statistics use **all matching cases**, not
the example sample. Sampling is stated explicitly.

Diagrams and example timelines show at most 24 events per path; activity
buttons and statistics still cover the full path. Variants with identical
display prefixes remain separate. The transition table shows at most 64
matching transition types and warns if more are omitted. A selected activity
filters transition rows; case examples continue to show their full bounded
chronology for context.

The additive API fields are `variants[].variant_id`, `activities`,
`transitions`, `median_case_elapsed_seconds`, `case_examples`, and
`case_examples_truncated`. Case summaries include `variant_id` and bounded
`events` with position, activity, timestamp, and preceding elapsed gap. These
ordinal variant IDs and case labels apply only within this response; they
are not persistent process or business-record identifiers. The shared Jira
projectors also receive these structural fields without extra provider reads.

**Metrics do not prove:** waiting time, active labor, rework cause,
successful business outcome, financial ROI, approved policy, or authorization
for any agent action. Rows are the events the source chose to export; gaps
and exclusions may make the process incomplete.

## Security and retention

- Manual, local-only and opt-in. No network request until the user clicks.
- Same local dashboard auth as existing `/v1/` API reads.
- Maximum 1 MiB request body (bounded even for chunked requests), 5,000
  events, 1,000 cases, and 64 activity types.
- CSV accepts *only* `case_id,activity,timestamp` and optional `source`;
  rejects extra data-bearing columns, malformed dates, rows or input.
- Activity labels must be short structural labels. Activity names are shown
  as entered and should never contain customer names or sensitive data.
- Raw case IDs and source column values never leave the API as output.
  Output uses temporary ordinal labels (Case 1, Case 2, etc.).
- During preview, no import, source values, or mined result is persisted, sent to hosted AI,
  stored as OWG evidence, or written to a new database. Analysis state
  remains in the dashboard tab until it is cleared or unloaded.
- `Cache-Control: no-store` for the response. No new grants, background
  capture, provider integrations, or any source-system write.

## Explicitly retained local snapshots

Preview remains ephemeral. The separate **Retain or reopen a local event-log
snapshot** controls let a dashboard user explicitly authorize saving the analyzed
file for 1, 7 or 30 days. Save retains all validated structural activities,
timestamps, per-case ordering, summary metrics and input quality diagnostics.
Native case IDs and source values are discarded before storage. Ordinal case
numbers remain stable only within that snapshot; they cannot identify a business
record or join later imports. No observation, knowledge, AI access, source
connection or synchronization is enabled.

Use **List saved local logs** to reopen after a refresh. The diagram retains its
bounded examples; the separate case browser pages through every case and every
event, including paths longer than the 24-event diagram limit. **Delete** removes
the snapshot and all its stored cases/events. Saved designs and independent
exports remain separate. Expired snapshots cannot be read and are pruned before
snapshot operations and during normal history-retention cleanup. The same local
SQLite database is used; this creates no second evidence collector.

At most 20 snapshots are retained, each within the existing 1 MiB / 5,000 event /
1,000 case / 64 activity import bounds. Quota checking and inserts are serialized;
partial imports roll back. This is a bounded local event-log inventory, not a
high-volume enterprise ingestion pipeline. All retained-log endpoints require a
local dashboard session; ordinary API/MCP bearer access does not expose them.
Responses use no-store caching.

- `POST /v1/process-mining/datasets?retention_days=1..30`: text/csv and an explicit
  `X-OWG-Retention-Confirmed: true` header.
- `GET /v1/process-mining/datasets`: local inventory and expiry times.
- `GET /v1/process-mining/datasets/report?dataset_id=log_...`: saved summary.
  Optional `workflow_id` and exact `revision` together compare every retained
  case/path with that saved model without changing the snapshot. Selecting a
  catalog reference before reopening a snapshot uses this comparison.
- `GET /v1/process-mining/datasets/compare?baseline_id=log_...&current_id=log_...&comparability_confirmed=true`:
  [review full-history changes between two retained logs](PROCESS_DRIFT_REVIEW.md),
  with optional exact catalog reference, descriptive path mix, activity/transition
  incidence and elapsed timing changes. No case joins or automatic process updates.
- `GET /v1/process-mining/datasets/cases?dataset_id=...&after=0&limit=25`:
  up to 100 cases per page, with optional exact `activity` and `variant_id` filters.
- `GET /v1/process-mining/datasets/events?dataset_id=...&case_number=1&after=0&limit=100`:
  complete ordered case events in pages of up to 100. Repeats and tied timestamps
  remain distinct; their supplied order is not independently verified.
- `POST /v1/process-mining/datasets/forget?dataset_id=...`: requires an explicit
  `X-OWG-Deletion-Confirmed: true` header.

Native identity authoritativeness, log completeness, business outcomes, active
labor and causality remain unverified. Retention is an explicit separate action;
merely opening the page, choosing a file or analyzing does not retain an import.

## Remaining for full process-mining parity

This is deliberately the first usable, inspectable import—not a Celonis
equivalent. Needed later: native opt-in source log connectors, explicit
cross-system case joins, production-grade data retention/multiuser roles,
conformance analysis against approved process models, reliable throughput for
large event volumes, validated variant editing, deeper bottleneck and
throughput analytics, and source-verified end states.

A real desktop browser acceptance test with a consented/synthetic CSV,
security testing, and the full cross-platform CI are release requirements.

Data-quality diagnostics also count cases with tied timestamps, repeated
case/activity/timestamp entries and cases sorted from nonchronological input.
Ties retain supplied row order; repeats are never silently deduplicated. Counts
and structural matches must be interpreted with those ordering limits. Source
log completeness and case-identifier authoritativeness remain unverified.

A saved [process catalog](PROCESS_CATALOG.md) revision can be selected as the
reference for a new analysis. The returned structural comparison covers all
cases and full variants, with bounded displayed alignments. It does not
establish business-policy compliance or independently verified outcomes.
