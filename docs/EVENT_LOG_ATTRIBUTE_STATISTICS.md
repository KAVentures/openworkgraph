# Case-level numeric event-attribute statistics (R9)

The existing **local dashboard-session-only** event-log analysis API now accepts a typed
`event_attribute_statistic` case metric. It calculates the average, minimum,
maximum, median or 90th percentile (p90) of a selected **finite numeric event attribute within each selected full
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

`statistic` must be `average`, `minimum`, `maximum`, `median`, or `p90`.
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
