# R6 — Review import and append quality across batches

The local, dashboard-session-authenticated
`GET /v1/event-logs/{log_id}/quality` response now includes an additive
`batch_trends` object. It lets an analyst see whether the *supplied* CSV/XES/OCEL
batch has more rejected rows, skipped duplicates, missing resources or tied
timestamps than the previous batch, without exposing source values or storing
another copy of the events.

## Use the local dashboard

Open the **Event logs** workspace, select a retained log, click **Analyse**,
then expand **Data quality** and click **Compare batches**. The independent
dashboard panel reads the same session-authenticated local quality endpoint
only after you click. It shows a row for each of five quality metrics in each
batch: numerator, denominator, percentage, and percentage-point change (or
**Not comparable**), with explicit comparison status. Future-dated percentages
are shown per batch but never compared across different report timestamps.

At most the **latest 100 stored reports** are rendered in the table to bound
browser work; the panel states the full report count and missing-report count.
**Download shown batches CSV** exports only those displayed counts, rates and
comparison statuses, not source events, employee names, row samples, mappings
or raw provider fields. CSV cells are escaped against spreadsheet formulas.
The report remains local and read-only, with no new grant or business action.
Switching logs or replacing the analysis invalidates any in-flight response;
no stale report is rendered for another selection.

The dashboard JavaScript is served as a static local asset. The existing
quality endpoint continues to require an OWG dashboard session. The table is
a descriptive review aid, not a claim that different source batches are
statistically exchangeable or that upstream data are complete.

The existing `reports`, `current`, `log` and
`batches_without_report` fields are unchanged. No new authentication scope,
remote publication, background job or business action is added. AI bearer
tokens and MCP cannot read these local business logs. The dashboard now displays trends through an isolated script without changing
the existing workspace JavaScript. The same data remains available through
the authenticated REST response.

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

## Interactive per-batch rejection diagnostics

After selecting **Compare batches** in **Event logs → Analyse → Data quality**, use **Inspect rejection reasons for batch** to choose a displayed import/append batch. The drill-down reads the `reports` already returned by the same local, session-authenticated quality request; it performs no additional provider read or write. It shows the batch's accepted/rejected/skipped counts, creation time, a 12-character **transform** SHA-256 prefix (not a source-file digest), and up to 12 rejection categories ordered by recorded count. Omitted categories are counted explicitly. Missing historical reports display **No saved rejection diagnostics** rather than fabricated zeros; unknown reason breakdowns remain unknown.

Reason categories are counts of recorded validation categories and may overlap: **do not add them together as unique rejected rows**. Category names are untrusted labels rendered as text, not HTML, and are not included in the existing counts-only CSV. This does not reveal rejected source-row samples or establish complete provider history, causation, individual productivity, or live-provider/customer acceptance. The selection and drill-down are ephemeral and reset with each new comparison; switching to a different log cannot download a stale comparison.

The committed Node regression exercises missing/unknown reasons, a large bounded reason list, suspicious formula-like labels, overlapping reasons and the interactive local quality-response rendering. Native cross-platform and browser checks are still required on the exact PR head before merge.
