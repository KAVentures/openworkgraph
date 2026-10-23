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
- Identifiers and activity names are truncated at 200 characters.
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
| `GET /v1/event-logs/{log_id}/export?format=csv\|xes\|ocel` | Download the log. |

Analysis routes take an optional `case_type` for OCEL logs. Every response is
`Cache-Control: no-store` and is marked descriptive: it reports what the log
contains and makes no causal claims, verifies no business policy and
authorizes nothing.
