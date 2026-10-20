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
a file, and click **Analyze selected CSV**. No file is uploaded on page open,
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
- No import, source values, or mined result is persisted, sent to hosted AI,
  stored as OWG evidence, or written to a new database. Analysis state
  remains in the dashboard tab until it is cleared or unloaded.
- `Cache-Control: no-store` for the response. No new grants, background
  capture, provider integrations, or any source-system write.

## Remaining for full process-mining parity

This is deliberately the first usable, inspectable import—not a Celonis
equivalent. Needed later: native opt-in source log connectors, explicit
cross-system case joins, production-grade data retention/multiuser roles,
conformance analysis against approved process models, reliable throughput for
large event volumes, validated variant editing, deeper bottleneck and
throughput analytics, and source-verified end states.

A real desktop browser acceptance test with a consented/synthetic CSV,
security testing, and the full cross-platform CI are release requirements.
