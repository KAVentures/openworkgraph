# Priority-aware queue abandonment in process what-if simulation (R14)

OWG supports optional queue-timeout assumptions and priority-class scheduling in
its **local scenario simulator**. Previously these could be combined, but the
per-priority result displayed only cycle time and wait for the **survivors who
completed**. In an overloaded queue, low-priority cases might abandon and
disappear from those completed-case statistics. This improvement makes that
selection effect measurable rather than presenting apparent lower waits as a
process improvement.

## User journey

In an existing authorized event-log simulation scenario, enter ordinary
activity service-time/resource assumptions, one or more
`activities.<name>.queue_timeout_minutes` values and 2–5 priority classes with
literal names, declared shares and different ranks. Run `what-if` using the
existing authenticated endpoint. Neither model-fitting nor customer outcome
verification is implied by these user-entered settings.

Example additive parameters:

```json
{
  "cases": 1200,
  "distribution": "fixed",
  "activities": {
    "Review": {
      "minutes": 2,
      "resources": 1,
      "queue_timeout_minutes": 0.25
    }
  },
  "priorities": [
    {"name": "Urgent", "share": 0.2, "rank": 0},
    {"name": "Routine", "share": 0.8, "rank": 1}
  ]
}
```

When both priorities and at least one explicit queue timeout are configured,
the scenario result adds **`priority_outcomes`**, in declared priority order.
Every class has:

- `post_warmup_arrivals`: all class cases entering the model after the
  pre-existing 10% warm-up, including those that never finish.
- `completed`, `abandoned`, and `unresolved`: mutually exclusive counts
  that sum exactly to post-warm-up class arrivals. Unresolved includes cases
  left in an unfilled batch, not wrongly characterized as abandonment.
- `completion_rate` and `abandonment_rate`: counts divided by **all**
  post-warm-up arrivals in that class. A class with no such arrivals has
  a null rate rather than an invented zero.
- `abandoned_by_activity`: exact counts for each timeout-enabled activity,
  including zeroes; across priority classes the sum matches the activity-level
  post-warm-up abandonment metrics.
- `name`, `rank`, `declared_share`, and an explicit
  `selected_completions_only_for_cycle_time` warning.

The result also declares
`priority_outcome_basis=all_post_warmup_arrivals_in_modelled_priority_class`,
`priority_abandonment_is_simulated_not_observed=true`, and
`priority_outcomes_not_a_fairness_or_causal_estimate=true`.

The pre-existing `priority_classes[].cycle_time_seconds` and
`mean_wait_seconds_per_step` still describe only served/completed cases.
Use them with the new denominators and abandonment breakdown to avoid
mistaking customer attrition for faster work.

## Compatibility and interpretation

- If no priorities are configured, the ordinary abandonment report remains
  unchanged. If no timeout is configured, the existing priority-class output
  remains unchanged. No API input change or new permission is introduced.
- Priority classes are **modeling categories provided by the analyst**, not
  demographics, actual customer commitments, verified service entitlements,
  employee scores, or policy rules.
- Queue patience is a scenario assumption, not inferred from missing history.
  Abandonment might cause survivor bias in scenario elapsed times, but this
  does not prove actual customer attrition, real savings, regulatory
  discrimination, or a causal effect.
- The output is local, read-only, and does not create/cancel business
  transactions or authorize agents.
- No new capture, consent, source integration, work text, or organization
  sharing behavior is added.

## Acceptance

`tests/test_event_log_simulation_priority_abandonment.py` covers partition
identities and denominator correctness, unchanged no-timeout/no-priority
response shape, zero-abandonment cases, shared-pool activity totals and
existing authenticated backend `what_if` integration. CI must pass on
macOS, Windows and Ubuntu, alongside browser, packaging, accessibility,
scale, security and native tests, before merging.

This adds one measurable R14 simulator output; **full simulation parity**
still requires independently validated real-world outcomes and reliability
assumptions, rather than only successful synthetic tests.
