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
In the local saved-process catalog, select **Review human and AI operating costs**, choose the bundle, and inspect the summary, all-attempt coverage and paginated case evidence. Export the exact report or clear the review.

## Interpretation

All case/repeat slots remain in the denominator. The manual baseline is reused
for each candidate, not treated as more independent observations. Output
includes labor categories, human-touch bounds, declared business-outcome
coverage, exact paired costs and savings, and first-year versus recurring
conditional scenarios. Currency values are exact rational microunits
(numerator/denominator as decimal strings) to avoid rounding artifacts, including browser JSON precision loss. Fixed scenario amounts are also returned as decimal strings; the input still uses integer microunits. The dashboard displays amounts rounded to the nearest microunit when necessary; the downloaded report preserves exact values. Null bounds are unbounded
in the relevant direction; missing costs are never imputed as zero.

Forecasts assume the observed case mix and per-attempt costs transfer to the
declared annual volume. They are not verified realized savings or statistical
confidence intervals. Quality gates are reported alongside cost but do not
make any selection or change execution rights. No provider, business system
or external service is contacted.


## Partial adoption, break-even and declared outcome risk

The optional scenario.adoption object accepts adoption_case_counts (1–21
unique whole numbers from zero through annual_cases),
avoidable_manual_annual_microunits (default zero, at most the total manual
annual fixed cost), minimum_declared_success_basis_points (default 9000),
and maximum_declared_regression_basis_points (default zero).
If omitted, the workbench uses zero, quarter, half, three-quarter and full
volume (deduplicated); zero and full are always included for custom grids.

The mixed-adoption view keeps existing manual fixed costs during partial
adoption, adds the full alternative fixed cost on any positive adoption,
and removes only the explicitly declared avoidable manual fixed cost at
full adoption. No-adoption savings are exactly zero. The earlier full-
replacement scenario is displayed separately and assumes all manual fixed
costs disappear. Neither assumes real-world volume or capacity transfer is
verified. Candidate variable costs scale linearly at the enrolled case mix.

Exact integer break-even frontiers report inclusive partial-adoption volume
ranges with strictly positive lower/upper savings bounds (not statistical
confidence), and a separate full-adoption point because fixed-cost removal
can cause a discontinuity. Null means no finite bound was established;
empty means a known bound never becomes strictly positive in that domain.
A negative variable saving can produce a maximum rather than minimum
profitable volume. Zero adoption is not a profitable deployment.

Paired declared outcome review reports candidate success, success-count
change, regression and improvement bounds across every enrolled case/repeat.
Missing or unresolved outcomes are not assumed good or bad. The gates
operate on declared outcomes, not independent source-system evidence.
A low-cost candidate can fail outcome gates, and no gate grants rollout
permission. Released person-time bounds are separate conditional capacity
estimates, not realized financial savings or headcount reductions.
