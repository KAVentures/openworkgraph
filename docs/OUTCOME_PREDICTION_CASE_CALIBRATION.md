# Outcome-risk calibration by independent completed case (R19)

Predictive monitoring already reports risk reliability by **prediction prefix**
in each historical rolling window. An example case can have many recorded
steps and therefore many correlated risk predictions, yet just **one**
eventual outcome. Prefix counts are useful context but must not be used as
if they were independent labeled people or cases.

The authenticated event-log monitoring API
`POST /v1/event-logs/{log_id}/predictions/monitoring` adds an
`outcome_case_reliability` view when an outcome is selected and a
historical window contains scored cases. The existing prefix-level
`windows[].reliability` field remains unchanged for compatibility.

## Case-level computation

For every completed case with at least one eligible scored prefix and a
consistent outcome label:

1. Average its scored probabilities into one **case mean risk**. This is an
   across-prefix summary of historic predictions, **not** a single
   prospective prediction at one decision point.
2. Assign the completed case to exactly one of five risk bands:
   0–20%, 20–40%, 40–60%, 60–80%, and 80–100% (the last includes 100%).
3. For each band with **at least 20 distinct completed cases**, report
   case count, number of original prediction points, average case-mean risk,
   observed outcome share, a 95% Wilson interval over **cases**, and a
   case-averaged Brier loss.
4. Suppress bands with fewer than 20 cases. Return only the number of
   suppressed nonempty bands, not their detailed rates or identifiers.

The view uses the latest **supported/scored** rolling window. It records that
window number. Cases appearing in multiple activity states contribute only
once here, unlike an activity breakdown. Invalid missing labels or unsupported
predictions are excluded. A case with inconsistent outcome labels cannot
enter the calibrated counts.

The API includes:
- `total_distinct_scored_cases`, the denominator for independent
  observations;
- `total_scored_prediction_points`, which may be far larger;
- `minimum_distinct_completed_cases_per_displayed_bin=20`;
- `withheld_low_support_bins` and a distinct-support status;
- `unit_of_uncertainty=independent_completed_case_not_correlated_prefix`;
- `source_outcomes_independently_verified=false`;
- `recalibration_applied=false`.

The UI shows the case-level table underneath the historical error breakdown
only when the analyst requests an outcome prediction. The panel explains
what is withheld and why.

## Interpretation and limits

This is descriptive calibration on an *existing event log*. It does not
fetch or independently verify source-system outcomes. The intervals cover
the **observed** outcome share in each band, not uncertainty in the
individual predictions or evidence that any intervention caused an outcome.
Calendar drift, incomplete event logs, case censoring, differing activities
and volume shifts can invalidate future predictions.

The average of all scored prefix probabilities does **not** replace the
live single-point probability. This is a retrospective quality check only.
Nothing is automatically recalibrated, executed, disclosed, uploaded or
authorized. No employee or case identifiers are included in the grouped
results.

## Tests

- `tests/test_event_log_prediction_case_reliability.py`: correlated
  prefixes cannot inflate the independent case count, low-support bands
  refuse detail even with 1,000 prediction points, cases are assigned
  to one band after their probabilities are averaged, and a real saved
  log only gets the view when outcome prediction is requested.
- `tests/browser/prediction_monitoring.cjs`: selects an outcome
  prediction and verifies the actual authenticated response, dashboard
  calibration rendering, low-support explanation and absence of source
  verification claims; keeps accessibility/mobile checks.
