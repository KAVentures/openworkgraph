# Human-in-the-loop operating economics (R26)

A cheap AI response is not necessarily a cheap completed task. The operating-cost
review combines exact frozen procurement trials with a manually observed
baseline and human intervention accounting. It is local and non-authorizing.

## Use

Run the existing repeated procurement trial, then export its plan and capture
ledger. Prepare one operating-cost bundle:

- `plan`: exact frozen plan
- `captures`: exact trial ledger
- `request`: format `openworkgraph.procurement-operating-cost.v1`, plan_sha256,
  ledger_sha256 from the recomputed trial review, currency, rates, manual,
  interventions, scenario and confirm_non_overlapping_cost_boundary (true).

Rates contain routine_hourly_microunits, review_hourly_microunits,
rework_hourly_microunits, fallback_hourly_microunits and
escalation_hourly_microunits in the trial currency. A microunit is one millionth
of that currency unit. Use fully loaded labor rates, not wages alone.

Manual records identify case_id and repeat; intervention records identify the
exact slot_id from the frozen schedule. Both contain routine_ms, review_ms,
rework_ms, fallback_ms, escalation_ms, other_direct_cost_microunits and
observed_business_outcome (success, failure, unresolved, or null).
These are non-overlapping summed human person-time measurements, not elapsed
agent response time. Enter an explicit zero only if no time/cost was incurred;
null or absent observations mean unknown. Record failed, timed-out, abandoned
and fallback work, not only successful deliveries.

Trial cost must describe provider/runtime spend only. Do not double count
labor, fixed charges or additional direct costs. The confirmation declares
the operator used that measurement boundary; OWG cannot verify the declaration.
Separate business-outcome declarations are not generated from shadow matches.

Scenario contains annual_cases, manual_annual_microunits, and
candidate_fixed_costs (one row per enrolled candidate with candidate_id,
annual_microunits and one_time_microunits). Annual and one-time amounts are
incremental fixed costs separate from variable provider and human costs.

```sh
python -m shared.agent_procurement_operating_cost --process-export process.json --bundle operating.json --out economics.json
```

The same bundle is supported by POST
`/v1/process-catalog/procurement/operating-cost?workflow_id=...&revision=...`
with an authenticated local dashboard session, JSON envelope
`{"document":"<bundle as JSON text>"}`. No retention; cache-control no-store.
No separate visual dashboard panel is included in this slice.

## Interpretation

All case/repeat slots remain in the denominator. The manual baseline is reused
for each candidate, not treated as more independent observations. Output
includes labor categories, human-touch bounds, declared business-outcome
coverage, exact paired costs and savings, and first-year versus recurring
conditional scenarios. Currency values are exact rational microunits
(numerator/denominator) to avoid rounding artifacts. Null bounds are unbounded
in the relevant direction; missing costs are never imputed as zero.

Forecasts assume the observed case mix and per-attempt costs transfer to the
declared annual volume. They are not verified realized savings or statistical
confidence intervals. Quality gates are reported alongside cost but do not
make any selection or change execution rights. No provider, business system
or external service is contacted.
