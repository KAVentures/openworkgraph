# Held-out router rehearsal (R26)

A category-specific portfolio assumes that a router can choose a destination
before a task is executed. This workbench measures the consequences of
declared routing predictions against a disjoint declared holdout, including
wrong classifications, manual leakage, abstention, missing predictions,
routing labor and direct costs. It is not a router or an actual execution test.

## Freeze

First create a discovery operating-cost bundle (plan, captures, request)
with an explicit portfolio. Create an exact held-out trial plan with distinct
suite/input hashes, unchanged candidates, currency and gates. Prepare:

- discovery: exact discovery operating bundle
- holdout_plan: exact frozen repeated trial plan
- request: portfolio_id, router_configuration_sha256, policy,
  confirm_manual_fallback_on_known_router_failure (true),
  confirm_non_overlapping_router_measurements (true), confirmed (true).

Policy defaults: minimum_distinct_cases_per_positive_category=3,
minimum_repeats=2, minimum_category_accuracy_basis_points=9000,
minimum_destination_agreement_basis_points=9000,
maximum_manual_leakage_basis_points=0, maximum_fallback_basis_points=1000,
minimum_declared_success_basis_points=9000,
maximum_declared_regression_basis_points=0,
require_complete_financial_evidence=true. Thresholds are exact integer basis
points. Retain the resulting plan before obtaining predictions.

```sh
python -m shared.agent_procurement_router_rehearsal router-freeze --process-export process.json --bundle freeze.json --out-dir frozen
```

## Input-only challenge (optional local CLI)

Supply discovery, holdout_plan, router_plan (exact frozen artifact), and suite
(the full reviewed holdout suite) in a challenge bundle. The installed offline
evaluator validates the suite before export. Explicitly confirm export of case
inputs. The candidate-facing challenge contains inputs, input hashes, exact
router slot IDs and the process specification, not reviewed labels, expected
answers, scoring gates, portfolio assignments or discovery evidence.

```sh
python -m shared.agent_procurement_router_rehearsal challenge --process-export process.json --bundle challenge-input.json --out-dir challenge --confirm-export-inputs
```

Keep the private challenge local unless authorized to disclose its inputs.
The CLI does not launch a candidate, transmit data or prove isolation.

## Review

Collect a prediction for each router case/repeat with exact case_id, repeat,
router_slot_id, status, predicted_category, direct_cost_microunits,
human_person_ms, elapsed_ms. Status is classified, abstain, timeout,
transport_failed, invalid_output or withdrawn. Only classified accepts a
category (normal, exception, missing_facts); others must use null. Unknown
measurements are null. Report all attempts including failures and manual
destinations. Direct routing cost excludes human labor, charged at the
declared routine hourly rate. Do not duplicate this in the agent or manual
observations.

The capture ledger has format
openworkgraph.procurement-router-captures.v1, router_plan_sha256,
router_configuration_sha256 and predictions. Missing predictions are not
assumed to have run a manual fallback; withdrawn is also unresolved.
Known abstentions and failures use a declared manual-path counterfactual.
No hidden expected answer or candidate scores are accepted in predictions.

Review bundle fields: discovery, holdout (operating-cost bundle with identical
scenario and rates, exact held-out plan and its own paired captures/manual
observations), router_plan, captures.

```sh
python -m shared.agent_procurement_router_rehearsal router-review --process-export process.json --bundle review.json --out-dir reviewed
```

The same freeze/review bundles are accepted by the local saved-process catalog
dashboard and POST /v1/process-catalog/procurement/router-freeze or router-review
with existing session authentication, workflow_id and revision query parameters
and the bounded JSON-text document envelope. Freeze requires latest revision.
The input-only challenge is CLI-only. Files are transient in the dashboard;
CLI outputs use exclusive private directories.

## Interpretation

The review recomputes discovery/holdout evidence, checks all plan/configuration
pins and unique slots, includes every enrolled attempt, and compares predicted
destinations with the fixed portfolio. Manual leakage means assigning an AI
agent to a case whose reviewed category was assigned manual. Missing routes
are bounded over all configured destinations. Manual against itself has zero
paired regressions/savings before router costs even if its own outcome is
unknown. AI critical and non-delivery evidence is retained separately from
declared business outcomes. Category gates include every observed category,
even one with zero declared annual volume; weighted gates use the frozen
positive volumes. Configured agents must pass whole-trial gates as well.

Conditional annual cost/savings use the true reviewed category mix specified
in the frozen scenario. Measured routing charges replace the portfolio's
hypothetical per-case routing charges and apply to every routing attempt.
All configured agents incur fixed charges, even if not chosen on a small
holdout. Manual fixed costs are retained because fallback remains possible.
No positive benefit or quality gate grants execution authorization.

Disjoint hashes are not proof of independent sampling or that predictions
preceded answer access. Router, manual and agent observations are customer
declarations. The replay uses paired arm outcomes, not actual routed
execution, and does not establish route classification accuracy in production,
causal benefit, realized cost savings or independent business outcomes.
