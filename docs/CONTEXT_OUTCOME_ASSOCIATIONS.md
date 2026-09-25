# Context/outcome associations

OpenWorkGraph can derive **descriptive associations** between task-context preflight linkage and later structural agent outcomes.

This layer is intentionally not a causal-effect estimator. It does not claim that OpenWorkGraph context made an agent succeed, fail less often, request approval more often, or otherwise change behavior.

## Endpoint

Authenticated local read API:

```text
GET /v1/task-context/outcome-associations
```

The normal API read credential is required. The write-only agent-ingest credential cannot read this endpoint.

The endpoint is aggregate-only. It does not return execution IDs, native run/session/trace/span IDs, context fingerprints, policy fingerprints, prompts, tool arguments/results, reasoning, or model output.

## Cohorts

The aggregate view keeps these linkage states distinct:

- `context_resolved` — the adapter reported that preflight succeeded and resolved a task context;
- `context_available_unresolved` — the context service was available, but no task context was resolved;
- `preflight_unavailable` — the adapter reported that preflight was attempted but unavailable;
- `not_observed` — no preflight linkage metadata was observed;
- `conflicting_assertions` — a run contained conflicting linkage assertions and is excluded from comparative strata.

`not_observed` does **not** mean "no context." Older runs may simply predate preflight instrumentation. The API therefore reports context exposure for that cohort as unknown.

## Outcomes

For each cohort OpenWorkGraph reports structural counts such as:

- run count;
- explicit run success count;
- explicit failure count (`error`, `denied`, `cancelled`);
- unknown-outcome count;
- success/failure fractions across all runs;
- success/failure rates among runs with known terminal outcomes;
- approval-request / approval-received run counts;
- policy-manifest-hash presence within linked context;
- first and last observed start times.

Unknown outcomes remain unknown. They are not silently converted into failures or successes.

## Comparisons

OpenWorkGraph does **not** produce a pooled context-vs-outcome difference across unrelated workflow families.

Comparisons are restricted to strata with the same:

1. observed structural workflow family; and
2. observation level.

Within such a stratum, `context_resolved` can be descriptively compared with:

- `preflight_unavailable`;
- `context_available_unresolved`;
- `not_observed`.

The first two have explicit preflight evidence. The `not_observed` comparator is weaker because historical context exposure is unknown.

Differences are suppressed unless both groups satisfy configured minimum run support and minimum known-terminal-outcome support. Raw counts remain visible even when differences are suppressed.

When support is sufficient, the endpoint may report **observed percentage-point differences** in:

- explicit success fraction;
- explicit failure fraction;
- unknown-outcome fraction;
- failure rate among known terminal outcomes;
- approval-request fraction.

These are descriptive differences only. They are not treatment effects.

## Policy breakdown

Within `context_resolved` only, the endpoint separately summarizes runs where the preflight snapshot reported a policy manifest hash versus runs where it did not.

No policy conclusion is drawn from unlinked or unavailable runs because their missing policy hash does not establish policy absence.

## Built-in interpretation limits

Every response states:

```text
causal_interpretation = false
effect_estimate = false
context_snapshot_server_attested = false
linkage_is_adapter_reported = true
```

Important limitations include:

- preflight linkage is adapter-reported rather than proof that the model consumed the context;
- context availability/resolution is not randomized;
- model, operator, workload, deployment, and time-period differences can confound observed rates;
- no-linkage-observed does not establish absence of context;
- family mismatches and conflicting linkage assertions are excluded from stratified comparisons.

The endpoint also exposes whether the two cohort time windows overlap. A non-overlapping window is an additional warning against interpreting the observed difference as an effect.

## Query controls

Supported query parameters include:

- `family_key` — optional structural family filter;
- `since` — optional evidence lower bound;
- `evidence_limit` — bounded canonical-evidence read window;
- `min_group_support` — minimum runs per cohort before percentage-point differences are emitted;
- `min_known_outcomes` — minimum known terminal outcomes per cohort;
- `min_stratum_support` — minimum total runs before a family/observation stratum is returned;
- `max_strata` — hard cap on returned strata.

All analytics are regenerated on demand from canonical evidence. This PR creates no learned-state table and writes no analytics results.
