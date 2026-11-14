# R14 — Shared-calibration comparison of simulation alternatives

OWG's existing local event-log workspace supports a calibrated baseline versus
one what-if scenario. This feature adds **two to four reviewed alternatives**
evaluated against **one immutable in-memory calibration**, not separate
calibrations that could silently reflect different source states.

## Dashboard

Open **Activity → Large event logs**, select an authorized saved log and
**What-if simulation → Load measured values**.

The **Compare several staffing alternatives** section lets the operator:

1. Set a bounded case count (10–5000) for each run.
2. Choose two to four named staffing alternatives; each adjusts the number
   of resources for one observed activity relative to the measured process.
3. Compare their modeled median cycle times, post-warm-up completed case
   counts and any reasons OWG withheld a direct numerical change.

The regular single-scenario editor remains available for advanced routing,
priority, batching, seasonality and resource-pool configurations. The
portfolio UI intentionally covers one common, understandable what-if decision.
Advanced API callers can vary multiple activity resources per alternative.

The selected local log stays local. Running this panel cannot approve a
process, deploy an agent, change staffing or send any private event text.

## API

`POST /v1/event-logs/{log_id}/simulation/portfolio` requires an existing
local **dashboard session**, as does the original `/simulate` endpoint.
JSON input is bounded to 64 KiB and disallows unknown top-level fields.

Example:

```json
{
  "format": "openworkgraph.simulation-portfolio.v1",
  "baseline": {"cases": 1000, "seed": 17, "activity_time_basis": "median"},
  "alternatives": [
    {"label": "Two reviewers", "activities": {"Review": {"resources": 2}}},
    {"label": "Four reviewers", "activities": {"Review": {"resources": 4}}}
  ]
}
```

Shared baseline inputs may include `cases`, `seed`,
`activity_time_basis`, `calendar` and a user-assumed
`arrivals_per_hour` if the selected log cannot calibrate arrivals.
Each alternative may override reviewed `activities`, `paths`,
`distribution`, `arrivals_per_hour`, `scenario_calendar`,
`pools` and `priorities`, with exact validation by the existing
simulation engines. Alternatives **cannot override case count or seed**;
names must be unique, printable and bounded.

This initial comparison endpoint uses the existing measured path routing.
Seasonal arrival patterns, transition routing, Monte Carlo resampling,
reconstruction uncertainty and custom context transitions remain accessible
through the existing single-scenario simulation interface, not this batch
endpoint. Unknown or unsupported option fields are refused rather than
quietly ignored.

The response includes a SHA-256 identifier for the shared calibration inputs,
the selected saved-log reference, baseline metrics, each changed alternative,
shared-scope flags, simulation assumptions and explicit caveats. The user can
compare results without requiring an automatic “best” choice.

## What direct deltas can mean

All cases are generated hypothetically. Using common seeded random streams
reduces variation from unrelated random seeds but does not make different
routes and arrival rates produce identical cases. **No causal savings,
validated production forecast, or real employee productivity conclusion
is inferred.**

OWG withholds direct median/p90/mean cycle-time deltas when either run has:

- fewer completed cases than its full post-warm-up population;
- an overloaded resource pool;
- simulated abandonment;
- cases left in unfinished batches.

It still shows each run's actual simulated completion count and cycle-time
estimates with the **reason comparison was withheld**. This avoids calling
a scenario “better” merely because some slower cases never completed.

The underlying time assumptions are those of OWG's existing calibration:
activity gaps can include queue time, resource labels may not reflect true
workers, arrivals are modeled, and event-log completeness is not
independently verified. A shared calibration does not remove those limits.

## Acceptance

`tests/test_event_log_simulation_portfolio.py` covers one calibration call,
shared seeds, deterministic repeated runs, identical alternatives, bounds,
unrecognized/sensitive fields, incomplete populations, simulated abandonment,
overload, HTTP authentication, request size, no-store and refusal semantics.

`tests/browser/event_log_workspace.cjs` exercises the real dashboard's
two-option staffing comparison, its server response and non-execution flags.

All normal CI and packaging gates must pass on the exact PR head before merge.
