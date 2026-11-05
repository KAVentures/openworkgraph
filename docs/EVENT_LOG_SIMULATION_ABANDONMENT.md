# Queue abandonment in what-if simulations (R14)

OWG's process what-if simulator can now represent **customer-entered queue patience**: a case waiting to start a particular non-batched activity can abandon it after a chosen limit. This is an extra scenario assumption, **not inferred from missing historical events**. It does not alter recordings, saved event logs, approved process designs, agent authority, or source-system data.

## Use through the existing authenticated simulation endpoint

The local dashboard's authenticated endpoint remains:

`POST /v1/event-logs/{log_id}/simulate`

Import or select an authorized local event log as before. The user must already have a dashboard session; this change does not introduce a public or MCP read path. The route accepts the existing scenario format, plus an optional `queue_timeout_minutes` under the specific activity to be modeled:

```json
{
  "format": "openworkgraph.simulation-scenario.v1",
  "label": "Review queue with customer patience",
  "cases": 2000,
  "seed": 7,
  "activities": {
    "Review": {"minutes": 20, "resources": 2, "queue_timeout_minutes": 15}
  }
}
```

For an activity not present in the calibrated log, supply an approved model path and its processing time as usual. A timeout of `0` means a case waiting for service immediately abandons (unless a resource is freed at that exact instant); omitting it means no abandonment at that activity. The supported range is 0–525600 minutes. Invalid values, booleans, infinity/NaN, and nonnumeric strings are refused. `queue_timeout_minutes` cannot coexist with `batch` on the **same** activity, because a shared batch's removal/repacking contract is a separate modeling problem.

The browser UI currently exposes other scenario controls but does **not** yet have the queue-timeout field: the active variant-coverage PR owns `dashboard/event_log_workspace.js`. This milestone intentionally avoids that conflicting shared file. The endpoint and JSON/SDK clients can use the option immediately; a separate small dashboard integration should follow when that file becomes unclaimed.

## Queue semantics

- The limit starts when a case arrives at a non-batched activity and **has to queue**. A case entering service immediately is not subject to an abandonment timeout. A waiting case leaves at its deadline if it has not started service.
- A resource completion at precisely the same time as the deadline **wins**, allowing the queued case to start service. Earlier timeouts are processed before later completions.
- Timed-out queue entries are lazily removed before any resource takes a new case; they can never be served after abandonment.
- Each case may abandon at most once, and stops following the remaining route. It is **not** counted as completed, completed throughput, or a measured cycle time. Unfilled-batch cases are counted separately and not mislabeled as abandonments.
- The choice works with activity-specific queues, shared resource pools, priority queues, optional calendars and the existing route-selection models. Timeouts are wall-clock elapsed queue time; closures are not excluded from patience. Batch + individual abandonment on the same activity is deliberately rejected.
- When the option is absent, the existing model, random sequence, outputs and cycle-time calculations are unchanged.

## Output interpretation

When at least one activity has an explicitly configured timeout, the **scenario result** adds:

- `cases_abandoned`: all simulated abandoned cases, including warm-up cases.
- `cases_abandoned_after_warmup`: abandoned cases after the existing 10% warm-up.
- `abandonment_rate_after_warmup`: the latter divided by post-warm-up simulated arrivals, **not** by finished cases.
- `abandonment_by_activity`: for every configured activity, its timeout and post-warm-up abandoned count (including zero).
- `abandonment_is_simulated_not_observed: true`.

The unchanged baseline does not acquire abandonment unless explicitly modeled separately. The scenario report retains `simulated_not_observed: true`, `forecast_or_guarantee: false` and `execution_authorized: false`. Increased abandonment can deceptively lower median *completed-case* time by excluding slow customers; always review both completion throughput and abandonments rather than treating a lower median as a verified improvement.

## Testing and limits

`tests/test_event_log_simulation_abandonment.py` covers: byte-identical no-timeout outputs, overloaded queues and honest incomplete cases, exact-deadline completion precedence, pool/priority integration, rejection of bad values and incompatible batching, and the real existing `what_if` backend called by the authenticated route.

This is a finite discrete-event **assumption-based experiment**, not fitted customer patience, observed churn, a production SLA forecast, a causal intervention estimate or a worker performance score. Real validation needs independently observed patience/cancellation labels and held-out business outcomes. No background source polling, capture, privacy setting, or external action is enabled.
