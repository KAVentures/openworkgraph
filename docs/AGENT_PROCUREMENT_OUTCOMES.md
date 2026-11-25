# Controlled sandbox outcome rehearsal and rollout review

This is a portable offline evaluation of customer-controlled sandbox
observations, not a sandbox launcher or a live provider operation verifier.
Use after the R26 selection and generated artifact preflight. Keep the
reviewed outcome oracle separate from candidates, and use an independently
controlled observer to capture complete before/after snapshots and actions.
The tool checks observations but cannot attest that isolation, observer
identity, chronology or source provenance is genuine.

Use a bounded synthetic state inventory per case. Records have opaque
record_id and fields whose values are strings, integers, booleans or null.
Declare the exact expected full final state and allowed operation/record
pairs, including records that must stay untouched. Unexpected record
additions, removals or changes fail even when the intended target succeeds.
Any observed forbidden action fails even if the state is later restored.
Use separate reset instances for baseline and candidate; their initial
states must match the frozen starting state exactly. This evaluator does
not infer state outside the selected inventory or enforce action isolation.

Design fields: format openworkgraph.procurement-outcome-design.v1,
expected_selection_receipt_sha256, expected_preflight_sha256, owner_alias,
sandbox_configuration_sha256, confirmed:true, cases and policy.
Each case contains case_id, category, starting_state, expected_final_state,
permitted_actions and reviewed:true. A snapshot is {"records":[{"record_id":
"target","fields":{"state":"open"}}]}. Permitted action entries are
{"operation_id":"close","record_id":"target"}. Policy fields:
minimum_distinct_cases (default 5), minimum_candidate_success_basis_points
(default 10000), maximum_paired_regression_basis_points (default 0),
require_all_categories (default true).

```sh
python -m shared.agent_procurement_outcomes freeze --process-export process.json --decision-bundle decision.json --preflight preflight.json --design design.json --out outcome-plan.json
python -m shared.agent_procurement_outcomes challenge --process-export process.json --decision-bundle decision.json --preflight preflight.json --plan outcome-plan.json --confirm-export-inputs --out challenge.private.json
```

The challenge omits final state oracle and permitted action policy, but
contains raw starting states. Only share authorized case inputs with a
separately controlled sandbox. No candidate or source is contacted here.

Observation ledger format openworkgraph.procurement-outcome-observations.v1,
plan_sha256 and observations. Each observation supplies case_id, arm
(baseline/candidate), slot_sha256, configuration_sha256,
sandbox_configuration_sha256, observer_alias, isolated_run_id, started_at,
ended_at (timezone-aware ISO datetime), status (completed, timeout,
transport_failed or withdrawn), initial_snapshot, final_snapshot (nullable),
observed_actions and action_capture_complete (boolean). Every enrolled slot
remains in the denominator. A missing/withdrawn or incompletely captured
successful-looking case is unresolved; observed harm or non-delivery is a
failure. Reused run IDs and mismatched starting states are rejected.

```sh
python -m shared.agent_procurement_outcomes review --process-export process.json --decision-bundle decision.json --preflight preflight.json --plan outcome-plan.json --observations observed.json --out outcome-review.json
```

The review exports per-case state delta IDs (not values), forbidden action
counts, full-population bounds and paired regression bounds. A separate
explicit decision uses request fields decision (request_separate_rollout_review,
retain_baseline or defer), reviewer_alias, expected_outcome_review_sha256,
expected_plan_sha256, rollback_plan_sha256 (nullable), confirmed:true.
A request for further rollout review requires passing checks, a distinct
declared reviewer alias and a rollback plan hash.

```sh
python -m shared.agent_procurement_outcomes decide --process-export process.json --decision-bundle decision.json --preflight preflight.json --plan outcome-plan.json --observations observed.json --request review-request.json --out rollout-review-receipt.json
```

The output is a request for a separate human-controlled rollout review,
never authorization. The rollback hash does not verify the rollback plan
contents. The original local artifact files are not reverified in this
step; the exact supplied preflight pin and recomputed selection are checked.
No actual production deployment, source read, business write or autonomous
execution occurs. Use the existing R17/R18 deployment lifecycle separately.
Private exclusive files, 8 MiB input bounds, 50 cases and two arms.
