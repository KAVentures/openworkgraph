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


## Batch quality review queue

The same **Compare batches** action now renders a **Batch quality review queue**
above the trend table. It is derived entirely from stored report counts, with
no additional event scan, provider read, cloud upload or permission. Each batch
has one of four descriptive statuses:

- **Recorded issue — inspect**: at least one *recorded* count is positive
  (rejected input, skipped duplicates, missing resources, tied timestamps or
  future-dated events). This is a reason to inspect the batch, **not** proof of
  a provider defect or a quarantine action.
- **Incomplete evidence**: no recorded positive count, but one or more
  diagnostic rates or the declared transform/comparison is unknown,
  inapplicable or incomparable. An empty denominator is not a clean bill of
  health.
- **Missing quality report**: the import/append batch exists but its historical
  report is absent. No diagnostic values are invented.
- **No recorded issue (not source-complete)**: all five recorded metrics have
  known, zero counts and a known transform; this does not prove the source
  export is complete or accurate.

The queue is ordered by batch, bounded to the latest 100 entries in the
browser, and identifies which recorded diagnostic fields are positive and
which evidence fields are unknown. **Inspect batch** navigates directly to
the existing per-batch rejection diagnostics for batches with saved reports.
Missing-report entries cannot be inspected as though they had report content.
Switching the selected log clears the queue and invalidates in-flight results.

REST consumers receive an additive `batch_trends.review_queue[]` array with
`batch`, `status`, `flags`, and `unknown_fields`. The status and field
names are bounded vocabulary strings, not source-row values. The response
retains the existing `batch_trends.format` version because this is additive,
and `interpretation.review_queue_is_descriptive_not_quarantine=true`
prevents interpreting the queue as a write, source-completeness assertion or
automatic quarantine decision. No new endpoint, background sync or role
exists. Synthetic tests exercise recorded problems, absent reports, empty
denominators, legacy transforms, browser rendering, direct inspection and
untrusted status labels; real-provider/customer validation is still required.


## Compare any two reported batches

The **Compare two selected batches** controls in **Event logs → Analyse →
Data quality → Compare batches** let an analyst choose a baseline import/append
report and a later target report from the latest 100 displayed stored reports.
Both controls are keyboard-operable, labeled selects. The panel uses the
already-loaded, authenticated, counts-only `batch_trends.batches` data; it
does **not** fetch source rows, issue a new provider request or persist a review
decision.

For each of the five metrics, the panel shows the baseline and target
numerator, denominator and rate. It displays a percentage-point difference
only when **all** of these conditions hold:

1. The baseline precedes the target, and both appear in the displayed window.
2. Every intermediate batch number has a saved report (no gaps).
3. Every report from baseline through target has the same valid declared
   SHA-256 transform digest.
4. That particular metric has nonnegative integer counts, a positive integer
   denominator and a numerator not exceeding its denominator.

The future-dated-events metric **never** receives a cross-batch delta because
its report-time reference instant differs, even when transforms match.
Unknown counts or denominators suppress only their affected metric deltas.
Missing intermediate reports, changed/unknown transforms, duplicate batch
identifiers, reversed selections and comparisons outside the displayed
100-report window suppress the entire comparison with an explicit reason.
Rates are recomputed from validated recorded counts, not trusted as arbitrary
display strings. The existing adjacent-batch REST deltas and CSV export remain
unchanged; this is an additional local visualization, not a new REST query or
unbounded event-log scan.

The panel makes no statistical equivalence, completeness, root-cause,
quarantine, worker-performance or business-action claim. A same-transform
difference is descriptive only: two supplied batches may still have different
source populations. Switching logs or rerunning the analysis clears the pair
selection and results, and a stale response cannot repopulate another log's
comparison. Node regression tests cover a three-batch baseline, DST-agnostic
recorded count differences, missing intermediates, transform changes, unknown
denominators, malicious identifiers, bounded display, fake-DOM keyboard
selection and stale-log clearing. These synthetic tests and the dashboard
implementation still require native CI and real consenting-customer validation
before a release claim.

## Review the largest recorded rate increases

After clicking **Compare batches**, use **Largest adjacent-batch rate increases**
to review the biggest recorded percentage-point increases across the latest 100
displayed stored reports. Choose a minimum increase of **1, 5, 10 or 20
percentage points**. The ranked table shows the batch, metric, preceding rate,
current rate and increase. At most 50 ranked rows are displayed, with the
total matching count and examined report count disclosed. **Inspect batch**
moves focus to the existing stored rejection-diagnostics selector; it does
not load provider data, quarantine a batch or save an approval.

This is intentionally narrower than the arbitrary two-batch comparison:
only **consecutive** reports with identical, verified SHA-256 transform
digests qualify. Both numerator and denominator must be nonnegative
safe integers, the denominator positive and the numerator no greater than
the denominator. Rates are recomputed from recorded counts. Missing batches,
changed/unknown transforms, invalid or missing denominators and future-dated
metrics (different report reference times) never create ranked deltas. A
positive increase is a **descriptive review candidate**, not proof that
upstream data quality deteriorated: source populations, sampling and event
coverage may differ. No employee ranking, statistical significance,
automated alert, provider sync or business action is inferred.

The table is calculated locally from the existing session-authenticated
counts-only `batch_trends.batches` response; no REST/MCP contract change,
additional event scan or persistence is introduced. Switching logs or
re-running the analysis clears the ranking and disables stale drilldown.
Synthetic Node tests cover rank ordering, thresholds, transform and
denominator refusals, missing batches, 100-report/50-row bounds and actual
fake-DOM threshold and inspect interactions. Native exact-head CI, including
cross-platform and browser jobs, and independent consenting-customer
acceptance are still required before a shipped-feature claim.
