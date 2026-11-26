# Paired router version change control

Use this after a portfolio and a routing policy have been specified. It
compares an incumbent and a challenger configuration on the same exact
holdout, not different cohorts or different portfolios. The operation is
local and non-authorizing.

## Freeze

Bundle fields: discovery (exact operating bundle), holdout_plan, request.
The request has incumbent_configuration_sha256 and challenger_configuration_sha256
(distinct), portfolio_id, router_policy (see router rehearsal), comparison_policy,
nominator_alias, incremental_annual_switch_cost_microunits,
incremental_one_time_switch_cost_microunits, and three explicit cost/fallback
confirmations: confirm_manual_fallback_on_known_router_failure,
confirm_non_overlapping_router_measurements,
confirm_non_overlapping_incremental_switch_cost; also confirmed=true.
Switching charges are nonnegative incremental costs not already measured in
router attempts or shared portfolio fixed charges.

Comparison policy defaults: minimum_paired_success_change_basis_points=0,
maximum_new_outcome_regression_basis_points=0,
maximum_new_manual_leakage_basis_points=0,
maximum_new_shadow_critical_basis_points=0,
maximum_new_non_delivery_basis_points=0,
maximum_new_fallback_basis_points=0, minimum_first_year_savings_microunits=0,
require_nonnegative_first_year_savings=true,
require_distinct_reviewer_alias=true. A disabled financial gate must not
specify a positive minimum savings threshold. The challenger must also
satisfy its absolute frozen router policy.

```sh
python -m shared.agent_procurement_router_change router-change-freeze --process-export process.json --bundle freeze.json --out-dir frozen
```

The result contains two ordinary frozen router plans with different slot
hashes. Retain the result before capture; use each sub-plan with the existing
input-only router challenge CLI if needed.

## Assess

Bundle fields: discovery, holdout (exact paired operating bundle),
change_plan, captures (object with incumbent and challenger ledgers).
Both ledgers must match their respective frozen configuration and slots.
Missing and withdrawn attempts remain unresolved. Recompute all evidence
rather than accepting imported scores. Repeated case/repeat pairs are not
independent statistical observations.

```sh
python -m shared.agent_procurement_router_change router-change-assess --process-export process.json --bundle assessment.json --out-dir assessed
```

Same destination means the same shared counterfactual arm: uncertainty in
its cost, time, outcome and critical/non-delivery status cancels in paired
comparisons. Different destinations use conservative bounds; missing
predictions enumerate all feasible configured routes. Router measurements
remain version-specific and never cancel merely because the destination
agrees. Compare newly introduced outcome regressions, manual leakage,
critical cases, non-delivery and fallback, not just net averages. The gates
apply to each observed category (including zero-volume categories) and to
the annual-volume-weighted mixture. Costs use exact rational microunits and
subtract declared switching charges; the common portfolio fixed cost is
shared. Positive savings cannot override the quality gates.

## Separate decision receipt

Add request to the assessment bundle, with disposition (one of
refer_for_controlled_change_review, retain_incumbent, defer), reviewer_alias,
expected_plan_sha256, expected_report_sha256 and confirmed=true. The tool
recomputes the assessment, checks both pins and the declared reviewer
separation policy, and refuses positive referral unless all required checks
pass. Retain/defer remain available for incomplete or unfavorable evidence.

```sh
python -m shared.agent_procurement_router_change router-change-decide --process-export process.json --bundle decision.json --out-dir receipt
```

All three operations are also available in the local saved-process dashboard
and authenticated no-store POST /v1/process-catalog/procurement/{operation}
with exact workflow_id and revision query parameters. Freeze and decision
require the latest revision; historical assessment remains inspectable.
CLI output is exclusive and private.

This is conditional change review, not statistical noninferiority, actual
routed execution, independently verified reviewer identity or proof of
precommitment chronology. No provider, deployment, agent, or business
system is called or changed. A positive referral is not execution permission.

After an independently authorized external pilot, use the [controlled pilot evidence review](AGENT_PROCUREMENT_ROUTER_PILOT.md) to review declared execution and outcomes, with version attribution and rollback-review triggers. The referral alone is never execution authority.
