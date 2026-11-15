# R19 — Still-open case coverage in prediction monitoring

## Purpose

Temporal prediction-quality monitoring scores only cases that have reached a
selected end activity. A good-looking historical backtest can therefore be
misleading when many of the cases being worked today have not yet finished.
This is a separate diagnostic of **selection and exact-state coverage**, not
a new forecasting model or a censoring correction.

Select an event log, choose the **Predict open cases** panel and select
**Monitor quality over time**. Below the activity errors, **Still-open cases
excluded from monitoring** shows:

- the number and share of selected cases still open (not scored in completed-case
  monitoring);
- still-open cases with and without at least the configured `min_support`
  completed-case examples at the **same latest activity and step**;
- still-open cases with **zero** comparable completed-case examples;
- up to eight most populated open-case activity/step states, each with counts
  of unfinished cases and distinct matching completed-case training prefixes.

The counts cover **all states** before the eight-row display cap; the interface
explicitly says when states have been omitted from the displayed table. It
contains no case IDs, worker names, text content or live source records.

## Semantics

`POST /v1/event-logs/{log_id}/predictions/monitoring` retains the existing
request shape and adds `open_case_coverage` to the result. The diagnostic
runs against the same selected cohort, using the temporary `pc` and `pp`
tables already prepared for the existing completed-case monitor. It does
not trigger a new data import, extra provider request, model training,
recalibration or business action.

A completed case contributes a prefix to an activity/step pair only when that
prefix is *before* its final end activity. The open-case census uses the last
observed activity and caps step at the same `MAX_STEP` as live prediction.
Support counts are **distinct completed cases**, never duplicated event
prefixes or people. A state with fewer than `min_support` exact analogues
would need a broader fallback or abstention. Even a supported state is **not**
proof of predictive calibration, especially if open cases are systematically
longer or take different branches.

All finished-case quality metrics and the chosen completed-case estimator
remain unchanged. The diagnostic is read-only and makes these flags explicit:

- `open_cases_contributed_outcome_labels: false`
- `censoring_adjustment_applied: false`
- `causal_or_future_accuracy_claim: false`

The log must already exist locally. Completion depends on the selected end
activities and the imported source's completeness. There is no assumption
that an uncompleted case has failed, or that an open state will eventually
finish. Independent real-world outcomes and censoring-aware temporal
backtesting are still needed before claiming future calibration.

## Acceptance

`tests/test_event_log_prediction_monitoring_open_case_coverage.py` covers
180 completed cases, 5 open cases with exact-state analogues and 7 unmatched
open cases; no-open baseline; no case identifiers in the output; and unchanged
live predictions after monitoring. It also validates UI disclosure.

Full native CI is required before merge. Synthetic fixtures do not establish
a real consenting deployment or calibrated clinical/business decisions.
