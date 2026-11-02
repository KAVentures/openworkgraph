# Prediction monitoring

Predictions for open cases come with one temporal backtest at one cutoff. Monitoring asks whether prediction
quality *holds over time*. It is a review aid, not an automatic change: a flag or proposal never alters live
predictions.

`POST /v1/event-logs/{log_id}/predictions/monitoring`, with a dashboard session, takes the prediction settings
plus `windows` (2–12, default 6). For example:

```json
{"end_activities": ["Close"], "windows": 6, "outcome": {"kind": "duration_over", "seconds": 259200}}
```

## Method

- **Windows:** completed cases are split by when they ended into `windows` consecutive windows. Each window's
  predictions come from a model trained only on cases that ended before the window starts. It uses the
  completed-cases estimator and the same state fallbacks as live predictions.
- **Per window:**
  - the mean absolute error of the median remaining time, against a naive baseline;
  - how often the true remaining time fell inside the 10th–90th percentile range (nominally 80%), with a 95%
    interval;
  - for an outcome, the Brier score against the base rate.

  Prefixes of one case are not independent, so the coverage interval treats each case as one observation: its
  share of covered prefixes. A window with fewer than 10 predictable cases is not scored, and the reason is
  given.
- **Flags:** in the latest window only:
  - coverage whose interval lies below 80% (`ranges_too_narrow`) or above it (`ranges_too_wide`) by at least 5
    points;
  - error more than 25% above the first window (`error_rising`).

  Each window's interval is a 95% interval, so across many windows one can fall outside by chance, which is why
  flags use the latest window.
- **Reliability:** for an outcome, the latest window's predicted risk by fifths, against the share that
  happened.
- **Recalibration proposal:** the factor by which to scale each range's distance from its median so that 80%
  of the latest window would have been covered. Above 1 widens, below 1 narrows. It is checked on the window
  before. `proposed_not_applied: true`.

## Dashboard

**Monitor quality over time** in the Predictions section uses the same end activities and outcome as
**Predict**. It always scores completed cases, whatever the live setting. It shows:
- a plain summary of any flags;
- one row per window: cases scored, typical and naive error, coverage with its interval, and the Brier score;
- the proposal in words.

## Validation

`tests/test_event_log_prediction_monitoring.py` uses synthetic logs:
- **Slowing process:** a process that slows threefold is flagged as too narrow with rising error, and the
  proposal widens the ranges to reach 80%.
- **Steady process:** across three seeds there are no flags, and average coverage stays near 80%.
- **Narrowing variance:** a process whose variance collapses is flagged as too wide, with a narrowing proposal.
- **Outcome risk:** risk is scored with a reliability table.
- **Live predictions:** unchanged by monitoring.
- **Small logs and validation:** small windows are not scored, and seven invalid settings are refused.
- **Routes:** the real HTTP route needs a session.

`tests/browser/prediction_monitoring.cjs` runs the same slowdown in Chromium:
- the request carries no estimator;
- six windows are shown, with both flags and the proposal;
- WCAG 2.1 AA at 390 px width.

## Limits

Monitoring replays this log's history. It does not establish that future predictions hold, and it rates
predictions, never people. Recalibration changes only interval width around the median, not the median
itself.
