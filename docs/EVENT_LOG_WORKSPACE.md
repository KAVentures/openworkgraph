# Event-log workspace (CSV, XES, OCEL 2.0)

The event-log workspace imports business event logs that you export from your
own systems and analyses them on this computer with an embedded columnar
engine (DuckDB). It is meant for logs that are too large for the in-browser CSV
miner, which stays limited to 1 MB / 5,000 rows and keeps nothing.

Nothing is uploaded anywhere. Imported logs are written to
`<data dir>/event_logs.duckdb` (owner-only permissions) and stay there until you
delete them.

## Formats

| Format | Import | Export | Notes |
| --- | --- | --- | --- |
| CSV | yes | yes | You map the case, activity, timestamp and (optional) resource columns. |
| XES (IEEE 1849) | yes | yes | `concept:name`, `time:timestamp` and `org:resource`; other string-like attributes are kept (32 max, 500 chars each). |
| OCEL 2.0 JSON | yes | yes | Objects, object types, events and event-to-object relationships with qualifiers. |

Every log is stored object-centrically. A flat CSV or XES log becomes one
object type, `case`. For an OCEL log you choose the case notion (`order`,
`item`, ...) per question, and the overview reports which object types share
events.

Timestamps must carry a timezone (`Z` or an offset). Rows without one, or with
an unparseable time, are counted as rejected and never guessed. XML with a
`DOCTYPE` or entity declaration is refused before parsing.

## Limits

- 4 GB per import, 50 stored logs (delete one to import another).
- Identifiers, activity names and resource labels are bounded to 200 Unicode
  characters; qualifiers to 80. Oversized values, non-string identifiers and
  control characters are rejected instead of truncated or cleaned into another
  key. Nonblank identifiers and labels preserve case, Unicode spelling and
  surrounding whitespace exactly.
- Acceptance: a 10M-event, 1M-case CSV imports in about a minute and every
  analysis query returns in well under 30 seconds on a CI runner
  (`OWG_SCALE_ACCEPTANCE=1 pytest tests/test_event_log_scale_acceptance.py`).

## API

All routes need the local dashboard session (`Authorization: OWG-Session ...`).
AI bearer tokens and MCP clients cannot read or change imported business logs.

| Route | Purpose |
| --- | --- |
| `GET /v1/event-logs` | List stored logs. |
| `POST /v1/event-logs/import?format=csv\|xes\|ocel&name=...` | Import the request body. Requires `X-OWG-Local-Storage-Confirmed: true`. CSV also takes `case_column`, `activity_column`, `timestamp_column`, `resource_column` and `delimiter` (`t` for tab). |
| `POST /v1/event-logs/{log_id}/delete` | Delete the log and every row that belongs to it. |
| `GET /v1/event-logs/{log_id}/summary` | Events, cases, activities, time span and case-duration median/p90/mean. |
| `GET /v1/event-logs/{log_id}/object-types` | Object types, objects per type and shared events between types. |
| `GET /v1/event-logs/{log_id}/variants` | Most frequent variants with share and median duration. |
| `GET /v1/event-logs/{log_id}/directly-follows` | Directly-follows graph with start and end activities. |
| `GET /v1/event-logs/{log_id}/activities` | Activity frequency and rework (cases that repeat the activity). |
| `GET /v1/event-logs/{log_id}/kpis?sla_seconds=&automation_resources=` | Throughput p50/p90/p95, rework rate, SLA breach rate, automation rate and weekly completions. |
| `POST /v1/event-logs/{log_id}/conformance` | Align observed variants with a reference model (JSON body, below). |
| `GET /v1/event-logs/{log_id}/root-cause?outcome=...` | Case features associated with an outcome, with confidence intervals. |
| `GET /v1/event-logs/{log_id}/export?format=csv\|xes\|ocel` | Download the log. |

Analysis routes take an optional `case_type` for OCEL logs. Every response is
`Cache-Control: no-store` and is marked descriptive: it reports what the log
contains and makes no causal claims, verifies no business policy and
authorizes nothing.

## Analytics

The dashboard card **Large event logs** (Evidence tab) offers all of the
following after **Analyse**.

### KPIs

- **Throughput**: time from a case's first to its last event (p50, p90, p95,
  mean), plus completions per week.
- **Rework rate**: share of cases that repeat at least one activity.
- **SLA breach rate**: share of cases whose throughput exceeds the SLA you enter.
- **Automation rate**: share of events performed by the resources you list
  (for example `bot, system`). OWG does not guess which resources are automated.

### Conformance

The reference model is one or more accepted paths:

```json
{"format": "openworkgraph.reference-model.v1",
 "paths": [["Receive", "Review", "Approve", "Close"]]}
```

The most frequent variants (500 by default, `max_variants` up to 2,000; the
response reports the share of cases they cover) are each optimally aligned with the closest path, at a cost of one per *skipped*
reference step and one per *extra* observed step. Fitness per case is
`1 - cost / (trace length + shortest path length)`. The case-weighted mean
is reported together with the share of perfectly fitting cases, the steps
most often skipped or added, and the most common deviating variants. Up to
10 paths of up to 100 steps are accepted. The reference is whatever you
enter; it is not treated as approved policy.

### Root cause

Choose an outcome: case takes longer than N hours, case has rework, case
contains an activity, or case ends with an activity. Every case feature
(activity present, resource involved, case-object attribute value) that both
5+ cases have and 5+ cases lack is compared:

- outcome rate with and without the feature, and the risk difference;
- a 95% Newcombe hybrid-score interval for that difference;
- whether the interval still excludes zero after a Bonferroni correction for
  the number of features tested.

These are associations in the imported log. They are not causes: a feature
can be a consequence of the outcome (a fix step in slow cases, for example),
and temporal order is not checked.

## Literal case identity and qualified relationships

A case key is supplied data, not a guess about business intent. CSV grouping
uses the exact selected field. Repeated rows with that same literal case key
remain one declared case; different keys never merge because they share a
prefix or differ only in surrounding whitespace or Unicode spelling.

XES traces and events, and OCEL objects and events, require unambiguous unique
identifiers within the imported log. XES's generated identifiers for omitted
names/IDs must not collide with explicitly supplied values. Duplicate or
conflicting IDs reject the complete import; its transaction leaves no partial
log. The columnar loader checks staged uniqueness without retaining every XES
event ID in a Python set. Missing fields and invalid timestamps retain their
existing rejected-event accounting.

An OCEL event may have multiple qualified relationships to the same object.
Their original qualifiers are preserved in storage and export, but the event
appears once in that object's case history. Two distinct events with identical
activity/time stay separate. An event linked to two different objects can still
appear in both cases; projection event counts are counts within the selected
case notion, not necessarily the raw log's global event count. Unknown foreign
object references do not become associations to a normalized prefix key.

These checks apply to new imports. Previously truncated/normalized identifiers
cannot be recovered from stored data; reimport the original export to obtain
literal identities. Existing logs are not rewritten. None of these mechanical
checks verifies provider/account identity, authoritative business boundaries,
source completeness, people, business outcomes or permission to execute.

CSV headers are bounded to one million characters and must have nonempty,
unambiguous names, including casing. Automatic column renaming is refused.
OCEL JSON rejects repeated fields and nonstandard numeric constants instead
of silently taking one of two conflicting identifiers.

The KPI API accepts repeated `automation_resource` query parameters for literal
resource labels, including commas and surrounding spaces. The older
`automation_resources` comma-separated shorthand still trims each item.
The dashboard accepts a JSON string array for exact resource labels and
reference-model JSON for exact activity labels; its suggested reference is
JSON so separators and whitespace cannot change imported activity names.
CSV column selection also preserves literal header spelling.
