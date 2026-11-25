# Intervention impact

Opportunity scenarios and benefit plans state *modeled* value. Intervention impact measures what a saved event
log shows changed after a change went live, for example an automation, a new approval rule or a reorganized
team. It is local and descriptive. It adds no source access, uploads or model calls, and it never claims
causality or realized savings.

`POST /v1/event-logs/{log_id}/impact`, with a dashboard session:

```json
{"intervention_at": "2024-03-01T00:00:00Z", "metric": "cycle_time",
 "treated": {"resource": "A"}, "window_days": 30, "exclude_recent_days": 7}
```

## Periods

Cases are assigned by when they started. Cases that started before the intervention are the **baseline**; cases
that started on or after it are the **follow-up**.

- **Partly exposed cases:** baseline cases still running at the intervention are counted as
  `baseline_partly_after_intervention`. They stay in the baseline by default: they are the longest baseline cases,
  so dropping them would make the baseline look faster than it was. `exclude_spanning: true` leaves them out
  instead, and a test shows the bias this introduces.
- **`window_days`:** keeps the same number of days on each side.
- **`exclude_recent_days`:** leaves out follow-up cases that started in the log's last days, because they may still
  have been running when the log was exported.

Every case left out is counted by reason.

## Metrics and estimates

| `metric` | Per case | Unit |
| --- | --- | --- |
| `cycle_time` | first to last event | seconds (elapsed, not work time) |
| `rework` | an activity repeated | share of cases |
| `sla_breach` | cycle time over `sla_seconds` | share of cases |
| `activity_count` | events of `activity` | events per case |

For each period the analysis gives the mean (or share) and the median, and the follow-up minus baseline
difference. Each difference has a percentile bootstrap 95% interval: `rounds` of seeded resampling of cases, at
most 5,000 sampled cases per cell. Counts stay exact. An estimate needs at least 10 cases on each side. The same
`seed` reproduces the same intervals.

## Control cohort

`treated` is a [case filter](EVENT_LOG_WORKSPACE.md) for the cases the change applies to. `control` defaults to
every other case and must not overlap `treated`.

The **difference-in-differences** compares the treated cases' change with the control cases' change over the
same periods. It removes trends both cohorts share, such as seasonality or a company-wide staffing change. It
does not remove changes that affected only one cohort.

## Placebo check

The same estimate is computed at a false intervention date: the midpoint of the baseline, using only baseline
cases that finished before the real date. If that interval excludes zero (`pre_trend_detected`), the metric was
already moving before the intervention, and the reading says so.

## Dashboard

The **Intervention impact** section of a saved log takes these inputs:
- the date the change went live, in UTC;
- the measure, with the SLA hours or the activity to count where that applies;
- an optional window, and recent days to leave out;
- optionally the activity or resource the change applies to.

It shows a plain reading, the cases and means per period, the change with its interval, the control comparison
and the placebo result.

## Validation

`tests/test_event_log_impact.py` uses deterministic synthetic logs:
- **Step change:** a 48 h to 24 h step is measured as −24 h, inside a 95% interval that excludes zero, with no
  placebo trend.
- **Control cohort:** a shared 6 h speed-up plus an 18 h treated effect gives a difference-in-differences of
  −18 h. Without the control, the change is −24 h.
- **Pre-trend:** a steady decline is flagged by the placebo check.
- **Counting:** windows, recent and spanning cases are counted, and dropping spanning cases biases the baseline
  short.
- **Other metrics:** rework, SLA and activity counts are measured.
- **Reproducibility and validation:** too few cases withhold the estimate, seeded results reproduce, and twelve
  invalid settings are refused.
- **Routes:** the real HTTP route needs a dashboard session.

`tests/browser/intervention_impact.cjs` drives the section in Chromium:
- local date validation;
- a before/after comparison with the placebo check;
- a control-adjusted change for one team, about −24 h;
- fields that appear only for their metric;
- WCAG 2.1 AA at 390 px width.

## Limits

- **Not causal:** results are associations between periods. Other changes in the same weeks, such as volume,
  staffing, seasonality or missing events, can cause or hide a difference.
- **Not savings:** cycle time measures elapsed time, not effort or cost. Realized savings and causal effects are
  never verified here (`causal_effect_verified: false`, `realized_savings_verified: false`).

## Saved changes (change markers)

A change usually matters for more than one analysis. In *Intervention impact*, **Save this date as a change** records the go-live date with a label, such as "Automation went live", and **Saved changes** picks it again later. The picker fills the date and shows the label and note. A saved change outside the log's time span is marked as such.

Markers are the owner's notes on the timeline. They are not events of the log, never change its data, and are deleted with the log. A log keeps at most 200.

- `GET /v1/event-logs/{log_id}/change-markers` lists them, oldest first, with whether each lies within the log.
- `POST /v1/event-logs/{log_id}/change-markers` with `{"marked_at": ISO time with an offset, "label", "note"?}` adds one.
- `POST /v1/event-logs/{log_id}/change-markers/{marker_id}/delete` removes one.

All three are dashboard session only.

## Time-resolved intervention profile

Enable the optional profile to inspect fixed-width case-start bins around
the declared intervention. Configure 1–90 days per bin, 1–12 baseline and
follow-up bins each, an explicit negative baseline reference bin, and a
minimum case count per cell. If the main comparison has a finite window,
all selected bins must fit inside it. Bin -1 is immediately before the
intervention, bin 0 begins at it; boundaries are inclusive at the start
and exclusive at the end. The original before/after estimate and placebo
remain unchanged. No post-hoc optimal date selection is performed.

Each selected group has exact included/excluded case accounting and
per-bin support, mean, median, change from the reference mean, and
spanning-before-exclusion counts. If treated/control cohorts exist, the
profile reports their per-bin mean gap and its change from the reference
gap. Cells below the minimum support have null means/medians and dependent
changes are withheld. Visible event-span and recent-exclusion overlap
flags describe coverage limitations; they are not source completeness
checks. Cohort definitions can depend on later observed events. No per-bin
confidence intervals, significance testing, proof of parallel trends,
causal effect or verified savings are claimed.

Optional request field time_profile is an object containing bin_days,
baseline_bins, follow_up_bins, reference_bin (default -1), minimum_cases
(default 10). The existing impact/cases endpoint accepts relative_bin and
expected_profile_sha256 together for exact pinned bin evidence. The digest
covers the settings, selected group rows, metric values, timestamps and
visible span. Changed relevant evidence refuses later pages. The dashboard
includes bin drill-down, aggregate JSON and additional CSV profile rows.
Regression cases cover fixed bins, control gaps, boundary exclusions,
minimum support, pinned case pages and unchanged default analysis. Full
native and browser acceptance remain outstanding.
