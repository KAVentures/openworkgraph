# R6 — Review import and append quality across batches

The local, dashboard-session-authenticated
`GET /v1/event-logs/{log_id}/quality` response now includes an additive
`batch_trends` object. It lets an analyst see whether the *supplied* CSV/XES/OCEL
batch has more rejected rows, skipped duplicates, missing resources or tied
timestamps than the previous batch, without exposing source values or storing
another copy of the events.

The existing `reports`, `current`, `log` and
`batches_without_report` fields are unchanged. No new authentication scope,
remote publication, background job or business action is added. AI bearer
tokens and MCP cannot read these local business logs. Existing dashboards that
do not yet display trends continue to work; the data is available through the
authenticated REST response. A future dashboard visualization must be
coordinated separately because multiple active feature PRs own the shared
workspace JavaScript.

## Read a trend

Each `batch_trends.batches[]` entry includes the batch number, kind,
report timestamp, declared transform SHA-256 and five metrics. Every metric
has `count`, `denominator`, and a fraction `rate` between 0 and 1 or
`null` when it cannot be computed:

| Metric | Count | Denominator |
| --- | --- | --- |
| `rejected_input_rows` | Invalid supplied rows | Accepted + rejected + skipped duplicates |
| `duplicates_skipped_on_append` | Already-present events skipped during append (zero for initial import) | Accepted + rejected + skipped duplicates |
| `events_without_resource` | Retained batch events with no resource | Retained events in that batch |
| `events_sharing_timestamp` | Retained batch events sharing a timestamp in one case | Retained events in that batch |
| `future_dated_events` | Retained batch events later than that report's creation time | Retained events in that batch |

A batch's duplicate-payload diagnostic is **not** the same as skipped
duplicates: payload duplicates with distinct IDs can be retained. A tied-time
diagnostic is evaluated *within* the retained batch; it does not count
ties that become visible only after combining batches. Rejection reasons can
overlap and are not summed to calculate rejection rates. An empty batch or a
legacy report without enough counts returns `null`, never a fabricated zero.

`comparison` reports `previous_batch`, `status`, and
`delta_percentage_points`. The status is one of:

- `first_report` — no previous report.
- `missing_intermediate_report` — the preceding batch's report is absent.
- `unknown_transform` — one transform digest is missing or invalid.
- `transform_changed` — formats/mappings/UTC assumptions differ.
- `same_transform` — adjacent batches have identical declared transforms.

A numeric delta is provided **only** for adjacent, same-transform batches
where both denominators are known and nonzero. For example, a missing-resource
rate rising from 0.10 to 0.25 produces a `15.0` percentage-point delta.
`future_dated_events` never has a cross-batch delta: reports are created
at different times, so their future-event reference instants differ. A
`same_transform` status does not guarantee that the batches contain
comparable source populations; a delta describes the supplied batches, not
causality or a verified regression in the upstream provider.

The series is generated from **stored immutable counts-only reports**. It
does not scan raw source values again or retain new rows. It survives local
restarts with the event log and disappears when that log is deleted. Legacy
logs lacking quality reports appear under `batches_without_report`.
`batch_trends.interpretation` explicitly marks source completeness and
business-action authorization as unverified/false.

## Verification and limits

Synthetic fixtures cover changed mappings, skipped duplicates, malformed
timestamps, missing resources, missing report gaps, unknown legacy fields,
and local HTTP session authorization. These are not real provider sandbox or
consenting customer acceptance. To inspect in a local authenticated session,
read the same quality endpoint already used by the Data quality panel; no
extra import, storage consent or external connection is needed.

This does not measure the accuracy of a source system, completeness of
historical exports, performance of an individual employee, or the causal
effect of an intervention. Do not use rates to rank people or authorize
automation.
