# Priority-class completion-time targets in what-if simulations (R14)

The existing local `POST /v1/event-logs/{log_id}/simulate` simulation API accepts an
optional `completion_target_minutes` on each declared priority class. The target
is **supplied by the analyst**, never inferred as an actual business SLA.

```json
{
  "cases": 2000,
  "seed": 7,
  "priorities": [
    {"name": "urgent", "share": 0.2, "rank": 0, "completion_target_minutes": 60},
    {"name": "standard", "share": 0.8, "rank": 1, "completion_target_minutes": 240}
  ]
}
```

All the existing queue, shared resource pool, working-calendar, simulated
abandonment and route-selection behavior is unchanged. Non-preemptive priority
service is still by ascending rank. Targets affect **reporting only**, not
scheduling, capture or business actions. The baseline is still FCFS.

An explicit target must be finite and numeric (not a boolean), greater than
zero and at most 525,600 minutes. Omitting it for a class preserves the
previous output for that class. A scenario without any completion targets has
byte-compatible legacy simulation output, with no new target field.

The scenario result adds `priority_completion_targets`, with one entry
per class that declares a target. Each entry includes the number of
`post_warmup_arrivals`, `completed_within_target`,
`completed_after_target`, `abandoned`, `unresolved`, and
`known_target_misses`. The rule is **end-to-end elapsed wall-clock time
from that case's arrival to its final simulated completion**, including
queueing, closures, batch waiting and processing. A completion exactly at
the target counts as on time.

Two deliberately conservative rates are reported:

- `attainment_lower_bound` = on-time completions / *all* post-warm-up arrivals.
- `attainment_upper_bound` = (on-time completions + unresolved cases) / *all* post-warm-up arrivals.

Late and abandoned cases are known misses. Unresolved cases might finish
in time or not; therefore OWG reports bounds rather than quietly ignoring
them and overstating service quality. If a class draws zero post-warm-up
cases, both rates are `null` instead of dividing by zero. These rates are
model outputs, **not confidence intervals, observed SLAs or proven forecasts**.
A truncation of simulated routes remains separately disclosed by the
existing `routes_cut_at_path_limit` field; a finished truncated route does
not certify a completed business process.

Target reports are opt-in and locally accessible only through the existing
authenticated simulation API. They do not change user privacy preferences,
sharing settings, source credentials, recordings or workflow execution
authority. Tests cover validation, inclusive boundaries, unfinished and
abandoned cases, warm-up exclusions, backward compatibility and the
end-to-end local simulation API.
