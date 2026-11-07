# R19: Where prediction errors occur — activity-state review

The local **Process mining → Predictions → Monitor prediction quality** journey
now displays a bounded breakdown of historical remaining-time prediction quality
by the *observed activity at the prediction point*. This augments, rather than
replaces, the existing per-window calibration and drift metrics.

## What it measures

For the latest **scored** time window, the same trained historical prediction
model and fallback rules produce an evaluation for every eligible prefix of a
later completed case. For each activity state:

- Count distinct completed cases and scored prefixes.
- Average absolute remaining-time error, plus the naive historical median
  baseline.
- The fraction whose observed remaining time lay within the model's 10th–90th
  percentile interval, with a descriptive 95% normal interval.
- When an outcome condition was requested, the case-averaged Brier score and
  case-averaged base-rate comparison.

Multiple prefixes within the *same case and activity* are averaged first; one
case counts once toward that activity's confidence interval and support floor.
A case can still contribute to several different activities, so group case
counts **must not** be added as though disjoint.

## Disclosure and interpretation

- **Fewer than 20 distinct scored cases** for an activity: its name and metrics
  are withheld. Only the number of withheld activity states is returned.
- At most **8 supported states** are displayed, ordered by completed-case
  support then activity name. Additional states are counted, not rendered.
- No case IDs, event IDs, employee/resource IDs or example traces are included.
- The dataset and the analysis remain local, behind the existing dashboard
  session. No new outbound transport or permission is introduced.
- Activity labels are already present in the authorized local event log; they
  are displayed as *text* through the existing safe DOM renderer.
- This is a historical, **descriptive**, uncorrected subgroup view. It neither
  scores individual employees nor establishes causes or reliable prospective
  accuracy. The operator should consider data coverage, nonindependent states,
  multiple comparisons and novel process versions before acting.
- The analysis does not change learned models, predictions, scheduled jobs,
  capture, dataset retention, agent permissions or business operations.

## API extension (backward compatible)

The existing
`POST /v1/event-logs/{log_id}/predictions/monitoring` response adds the
`activity_quality` field. Its value is `null` when no window has enough
scored cases. Otherwise it includes:

```json
{
  "window": 3,
  "minimum_distinct_completed_cases": 20,
  "maximum_displayed_states": 8,
  "total_scored_activity_states": 2,
  "withheld_low_support_states": 0,
  "withheld_over_limit_states": 0,
  "unit_of_uncertainty": "completed_case_within_activity_not_correlated_prefix",
  "no_case_or_resource_identifiers": true,
  "activities": [
    {
      "activity": "Check order",
      "scored_cases": 45,
      "scored_prefixes": 60,
      "mean_absolute_error_seconds": 1200,
      "naive_mean_absolute_error_seconds": 1400,
      "p10_p90_case_averaged_coverage": 0.82,
      "p10_p90_coverage_ci95": [0.70, 0.94]
    }
  ]
}
```

Example numbers above describe a **hypothetical** response, not OWG pilot or
benchmark results. Exact returned values are data-dependent. Optional outcome
metrics only appear when requested.

## Tests

`tests/test_event_log_prediction_activity_quality.py` validates repeated
prefix case weighting, withheld rare states, deterministic top-eight caps,
bounds, missing-evidence behavior and unchanged live predictions. Full native
and browser CI remains the required merge gate.
