# Case-level numeric event-attribute statistics (R9)

The existing **local dashboard-session-only** event-log analysis API now accepts a typed
`event_attribute_statistic` case metric. It calculates the average, minimum,
maximum, median, 90th percentile (p90), total or population standard deviation of a selected **finite numeric event attribute within each selected full
case**, then lets an analyst aggregate those case values (for example, the
average of per-case average amounts, or the maximum case peak). This is useful
when an imported CSV/XES/OCEL log carries an explicitly mapped numeric value,
such as a recorded transaction amount or an observed queue-length reading.
It is **not** a new source connector, currency conversion, or verified business
outcome.

## API example

`POST /v1/event-logs/{log_id}/analysis` (same session authentication as other
event-log routes; `Authorization: OWG-Session <local-session>`):

```json
{
  "level": "cases",
  "metrics": [{
    "name": "case_mean_amount",
    "kind": "event_attribute_statistic",
    "attribute": "amount",
    "unit": "EUR",
    "statistic": "average",
    "missing_policy": "available"
  }],
  "measures": [{"fn": "avg", "of": "metric:case_mean_amount"}]
}
```

## Scoped totals and within-case variability

Use `"statistic": "total"` for a **sum of finite observed values per case**,
rather than multiplying an average by a guessed event count. Unlike the older
`event_attribute_sum` case metric, the typed statistic supports an exact
`activity` selector and `from_activity`/`to_activity` stage bounds (including
`stage_occurrence: last_completed`). This is useful for comparing the total
recorded `Pay` and `Refund` amounts within the same observed case, or
total values recorded in the first versus last completed review stage.

Use `"statistic": "population_stddev"` for the population standard
deviation **within each case's selected finite event values**, computed as
the square root of the mean squared deviation using population denominator
`N` (not sample denominator `N-1`). One valid observation has spread
zero; zero valid observations yield **null**, not zero. Neither metric
asserts source completeness. The declared unit is retained unchanged.

```json
{
  "level": "cases",
  "metrics": [
    {"name": "pay_total", "kind": "event_attribute_statistic",
     "attribute": "amount", "unit": "EUR", "statistic": "total",
     "activity": "Pay", "missing_policy": "complete"},
    {"name": "pay_spread", "kind": "event_attribute_statistic",
     "attribute": "amount", "unit": "EUR", "statistic": "population_stddev",
     "activity": "Pay", "missing_policy": "available"}
  ],
  "measures": [
    {"fn": "sum", "of": "metric:pay_total"},
    {"fn": "avg", "of": "metric:pay_spread"}
  ]
}
```

Both metrics use existing finite-value diagnostics. Under `available`,
invalid/missing/nonfinite event values are omitted and disclosed; under
`complete`, **any** such value in the selected activity/stage withholds
that case's metric. An observed total of zero is valid, while a case with
no matching rows or no finite values remains unknown. A numeric aggregate
that overflows to infinity is withheld, never exported as a valid total.
For OCEL flattening, an event linked to multiple selected case objects
contributes once to each object's case metric, not to a verified ledger.
A `sum` of case totals can therefore double-count business events across
objects and is not automatically a reconciled financial total.

## Activity-specific metrics (same case, different event types)

An optional exact `activity` field limits a statistic to events with that
activity *within each already-selected full case*. This is useful for asking
how much was recorded at `Pay` versus `Refund` without blending amounts
from unrelated events in the same case. For example, two typed metrics can be
subtracted with the existing `arithmetic` operation (only when declared units
match):

```json
{
  "level": "cases",
  "metrics": [
    {"name": "paid", "kind": "event_attribute_statistic", "attribute": "amount",
     "unit": "EUR", "statistic": "average", "activity": "Pay",
     "missing_policy": "complete"},
    {"name": "refunded", "kind": "event_attribute_statistic", "attribute": "amount",
     "unit": "EUR", "statistic": "average", "activity": "Refund",
     "missing_policy": "complete"},
    {"name": "difference", "kind": "arithmetic", "operation": "subtract",
     "left": {"kind": "metric", "name": "paid"},
     "right": {"kind": "metric", "name": "refunded"}}
  ],
  "measures": [{"fn": "sum", "of": "metric:difference"}]
}
```

This example sums **differences of per-case average values** only for cases
with usable Pay *and* Refund values. It is not a financial ledger total,
net revenue, currency conversion or evidence of complete payment capture.
For a case with no matching activity, the statistic is **null**, never zero,
even under `missing_policy=complete`. For `complete`, every event row
*matching that activity* must have a finite numeric value; unrelated case
events are not required to carry that attribute. Missing/invalid/nonfinite
matching values remain disclosed. The activity string is an exact,
case-sensitive bound parameter (not an SQL expression or substring).

The existing counts-only `cases_without_matching_activity` field identifies
selected cases with no matching activity (zero for unfiltered metrics).
`cases_with_no_finite_attribute` includes both cases with only invalid
matching rows and cases with no matching activity.
`flattened_case_event_rows_considered` and the finite/omitted row counts
refer **only to matching activity rows** when `activity` is specified.
`coverage_scope` changes to
`selected_full_cases_matching_activity_flattened_case_event_rows` in this
mode. Omit `activity` for the unchanged all-activity behavior.
Saved analyses and reusable metric templates retain the exact selector.

## Stage-bounded metrics (from one activity to the next)

An analyst can optionally supply **both** `from_activity` and `to_activity`
on an `event_attribute_statistic` metric. OWG locates the first observed
`from_activity` in each selected full case and the first **strictly later**
`to_activity`. It calculates the statistic over the events from the start
**inclusive** to the end **exclusive**. This enables a question such as
"What was the average observed amount between Receive and Approve?" rather
than blending earlier or later stages. A prior `to_activity` does not end
a stage; an end must occur *after* the selected start.

```json
{
  "level": "cases",
  "metrics": [{
    "name": "review_stage_amount",
    "kind": "event_attribute_statistic",
    "attribute": "amount",
    "unit": "EUR",
    "statistic": "average",
    "from_activity": "Receive",
    "to_activity": "Approve",
    "missing_policy": "complete"
  }],
  "measures": [{"fn": "avg", "of": "metric:review_stage_amount"}]
}
```

### First versus last completed stage (rework and repeated cycles)

The default `"stage_occurrence": "first"` preserves existing behavior: first
observed `from_activity` and first strictly later `to_activity`. For
repeated review/approval or rework cycles, explicitly set
`"stage_occurrence": "last_completed"` to select the **latest observed
start that has a strictly later end**. OWG calculates the statistic only
over that selected interval, not over every cycle and not over an unfinished
final cycle. A start without a later observed end is never treated as
complete; an earlier complete cycle can still contribute its value.

For example, a case with `Start(2), Work(4), End, Start(8), Work(10),
End, Start(100)` has a first-stage average of **3**, a last-completed
stage average of **9**, and one trailing unfinished stage. The final
`Start(100)` does not silently inflate the last-completed result.

```json
{
  "level": "cases",
  "metrics": [
    {"name": "first_review", "kind": "event_attribute_statistic",
     "attribute": "amount", "unit": "EUR", "statistic": "average",
     "from_activity": "Start", "to_activity": "End", "stage_occurrence": "first"},
    {"name": "last_review", "kind": "event_attribute_statistic",
     "attribute": "amount", "unit": "EUR", "statistic": "average",
     "from_activity": "Start", "to_activity": "End", "stage_occurrence": "last_completed"},
    {"name": "observed_change", "kind": "arithmetic", "operation": "subtract",
     "left": {"kind": "metric", "name": "last_review"},
     "right": {"kind": "metric", "name": "first_review"}}
  ],
  "measures": [{"fn": "avg", "of": "metric:observed_change"}]
}
```

This is a **per-case comparison of two observed intervals**, not a
source-authoritative rework count, financial adjustment, causal effect or
evidence of complete capture. If a case has no start or no completed
start→end pair, the statistic is null. If start and end are the same
activity, the later end occurrence can also be the start of an unfinished
next cycle; this is disclosed rather than inferred complete. Both
selectors use the same deterministic `(UTC timestamp, event_id)` ordering,
including ties. `last_completed` is valid only when **both**
`from_activity` and `to_activity` are specified. The bounded
parameterized planner uses a window pass rather than enumerating all
possible start/end pairs.

The optional `activity` selector composes with this interval: only
events of that exact activity *inside the interval* contribute. Both
boundaries are exact case-sensitive strings bound as SQL parameters. Events
are ordered by observed UTC timestamp and stable event ID to break ties;
when timestamps tie, ordering reflects the stored ID, **not proven
real-world causality**. No matching start, no later end, or no usable values
produces **null**, never zero. No start or end can be inferred from
unobserved source activity. The `available` versus `complete` missing
policy applies to the *selected stage rows*, not events outside it.

Coverage includes `cases_without_stage_start`,
`cases_without_later_stage_end`, and
`cases_without_complete_stage`; when `last_completed` is selected it also
includes `cases_with_trailing_unfinished_stage` (including cases with a
start but no completed stage). These counts survive the numeric-coverage
summary and remain distinct from
`cases_without_matching_activity` (which counts only complete stages
with no events matching an optional activity selector).
`flattened_case_event_rows_considered` counts only rows in complete
observed stages. The coverage scope explicitly includes
`stage_bounded`. Saved definitions and existing authenticated analysis
and export contracts retain both boundaries and the explicit stage occurrence selection. This does not prove that
source events are complete or that the named activities correspond to
approved business policy.

`statistic` must be `average`, `minimum`, `maximum`, `median`, `p90`,
`total`, or `population_stddev`.
`missing_policy` is `available` (default) or `complete`:

- **available:** use finite numeric values actually present in each case;
  a case with none returns null. This may produce partial-case values.
- **complete:** withhold a case's statistic unless *every matching flattened event row*
  in that case has a finite numeric value for the chosen attribute (all rows
  when no `activity` is specified). It does not assert that the original
  source system exported all its events.

For `median` and `p90`, DuckDB computes the **continuous interpolated quantile**
within each case's observed finite numeric values: the sorted values at
zero-based position `(n - 1) × 0.5` or `(n - 1) × 0.9`, linearly interpolating
between adjacent positions. For example, [2, 4, 8, 10] has median 6 and
p90 9.4. These are *within-case* distribution summaries, not percentiles
across cases; to compute a percentile of case values, select the corresponding
outer analysis measure separately. A case with one finite value has that
value as both quantiles.

Missing, nonnumeric, NaN and infinite values are excluded, **never replaced
with zero**. Case values that overflow or are nonfinite are withheld. Averages
are **per case**; the final `avg` of case averages weights cases equally, not
each underlying event equally. No user-controlled expression or attribute name
is inserted into SQL; attributes are bound JSON-path parameters.

Each metric response includes the existing `cases_with_value`,
`cases_without_value`, full definition and unit, plus:

| Coverage field | Meaning |
| --- | --- |
| `flattened_case_event_rows_considered` | Event rows across selected complete case traces (an OCEL event may belong to multiple distinct cases). |
| `event_rows_with_finite_attribute` | Rows with a parseable finite numeric value. |
| `event_rows_with_missing_invalid_or_nonfinite_attribute` | Rows omitted for missing, invalid or nonfinite value. |
| `cases_with_partial_attribute_coverage` | Cases with both finite and omitted rows. |
| `cases_with_no_finite_attribute` | Cases with no usable numeric value, including those without the selected activity. |
| `cases_without_matching_activity` | Selected cases with no matching activity; zero when no activity filter is set. |
| `cases_excluded_by_complete_policy` | Partial cases additionally withheld when `missing_policy=complete`. |
| `coverage_scope` | Fixed descriptor: selected full cases, flattened case-event rows. |

Existing `event_attribute_sum` semantics are unchanged. This additive
metric works with the same cohort, case notion, case filters, typed arithmetic,
saved analysis definitions, symbolic metric templates and analysis JSON/CSV
exports. A saved analysis pins the metric definition, **not** a frozen dataset
snapshot. All retained data remains in the existing local event-log workspace
and follows its deletion/retention lifecycle; no provider call, upload,
autonomous action or new permission is introduced.

## Validation and limitations

`pytest -q tests/test_event_log_attribute_statistics.py` checks real stored
CSV imports, finite/missing/invalid values, both missingness policies, all
five statistics, activity-specific filtering and absence versus invalid-value coverage, odd/even sample interpolation and invalid/nonfinite values, unit propagation through arithmetic, saved templates and
analysis replay, invalid/injection specs rejected before dataset access,
empty selection, and actual FastAPI session/bearer authorization.

The full repository's required Ubuntu/macOS/Windows pytest, browser, process
collaboration, scale, accessibility, starter and packaging/release workflows
must also pass on the exact PR head before merge. Synthetic tests are not
customer acceptance. No claim is made that input amounts share a currency,
that missing values are missing at random, that a recorded value reflects a
source-of-truth balance, or that a statistical association is causal.


## Dashboard: build scoped numeric statistics without writing JSON

Open **Large event logs**, import or select an authorized local log, and choose
**Analyse → Build an analysis → Cases**. In **Case metrics (optional)** choose
**Numeric event values within each case or stage**. Give the metric a unique
lower-case name, enter the exact imported numeric event-attribute key (for
example `amount`) and its **declared** unit (for example `EUR`), and select
one of: average, minimum, maximum, median, 90th percentile, total, or
population standard deviation. These describe finite numeric values **within
each observed case**; an outer measure (for example **Average** or **Total**)
then aggregates the per-case results. No currencies are converted.

Choose **Use available finite values** to omit invalid/missing values and
disclose their counts, or **Require every matching event to have a finite
value** to withhold incomplete cases. Optionally restrict the calculation to
one exact activity. To analyse a completed stage instead of the whole case,
select **both** a start and end activity and choose either **First observed
completed stage** or **Last completed stage**. The start is included and the
end is excluded; a trailing unfinished stage is never treated as complete.
Partial stage selections are rejected in the form before any request.

Click **Add metric to this analysis**, select a measure such as **Average
<metric>** or **Total <metric>**, and run. The dashboard sends the typed
`event_attribute_statistic` definition to the **existing authenticated
analysis API**; it does not generate or execute arbitrary SQL. **Save metric
for reuse** stores the exact selectors in the log's metric library; **Save
analysis** stores the definitions and selected measures together. Existing
JSON/CSV exports retain the resulting numeric values and disclosed coverage.
Re-running a saved analysis reads the current retained log; it does **not**
pin a source snapshot. Deleting the local log removes its saved definitions.

**Worked example:** an observed case contains `Start(2), Work(4), End,
Start(8), Work(10), End, Start(100)`. With `attribute=amount`,
`statistic=average`, and stage `Start→End`, **first** produces 3 and
**last completed** produces 9; the unfinished final start contributes to
neither. Another case with `Start(5), Work(invalid), End` contributes 5
under **available**, but is unknown under **complete**. Unknown is never
silently counted as a zero. These examples are observed log arithmetic,
not source-of-truth payment reconciliation or evidence of complete events.

The committed Chromium browser acceptance exercises the form, invalid
stage refusal, real local CSV import with an explicitly retained numeric
attribute, session-authenticated analysis requests, last-completed versus
first-stage results, saved metrics/analyses, CSV export and WCAG checks.
Native CI and real consenting customer acceptance remain separate gates.

## Dashboard: compare two typed case metrics without hand-written expressions

The same **Build an analysis → Case metrics** form now exposes all eight
already-supported version-1 pairwise arithmetic operations. Besides add,
subtract, multiply and divide, choose **Smaller of two values** (`minimum`),
**Larger of two values** (`maximum`), **Average of two values** (`average`),
or **Absolute difference (nonnegative)** (`absolute_difference`).
Each operand can reference an earlier named metric or a finite typed constant.
The resulting metric is itself reusable by the same saved-metric library,
saved analyses, grouped measures, and JSON/CSV export paths.

For example, add `first_review_total` and `last_review_avg` as two
`event_attribute_statistic` metrics with unit `EUR`, then add
`review_difference` of kind **Arithmetic on earlier metrics**, operation
**Absolute difference**, left `first_review_total`, right
`last_review_avg`. For one case with observed values 6 and 9 EUR, the
difference is 3 EUR; **Average of two values** would be 7.5 EUR.
For a case with one missing operand, both results are **unknown**, never
zero or the surviving operand. The per-metric coverage reports missing
operands, and division separately reports zero divisors.

All four new form choices require **identical declared units** (no currency
or physical-unit conversion). The server refuses mismatches before reading
data. Multiply/divide retain their existing unit composition; every operation
uses the existing bounded, parameterized, versioned expression planner, not
user-supplied SQL. This is a comparison of *recorded case values*, not a
reconciled financial difference or evidence of source completeness.

The Chromium regression uses the actual authenticated analysis endpoint,
selects all four operations in the form, verifies typed versioned requests,
observed numeric outputs, missing-case withholding, declared units, a saved
analysis replay, CSV export, invalid-operation refusal, and a mismatched-unit
refusal. The tests are committed; they are **not claimed as native-passed**
until the exact-head GitHub Actions checks execute.
