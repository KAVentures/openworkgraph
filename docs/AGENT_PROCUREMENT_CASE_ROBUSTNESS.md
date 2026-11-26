# Paired case-cluster robustness (R26)

A vendor-neutral procurement comparison can look strong because one easy or
highly repeated case dominates its reported score. The robustness review
recomputes the existing frozen trial and then reports sensitivity to whole
case clusters, workflow categories, and repeat blocks. It does not reselect
the population, generate new observations, or authorize a candidate.

## Run locally

Use the same saved process packet, frozen trial plan and capture ledger
as the existing procurement review. Obtain the ledger SHA-256 from the
recomputed trial report. In the local process catalog choose **Stress-test
paired candidate robustness** and upload one JSON document:

```json
{
  "format": "openworkgraph.procurement-case-robustness.v1",
  "expected_plan_sha256": "<exact frozen plan digest>",
  "expected_ledger_sha256": "<exact recomputed ledger digest>",
  "plan": "<replace with entire frozen plan JSON object>",
  "captures": "<replace with entire capture ledger JSON object>",
  "policy": {
    "maximum_case_deletions": 1,
    "minimum_remaining_cases": 2,
    "minimum_paired_match_change_basis_points": 0,
    "maximum_paired_regression_basis_points": 0,
    "maximum_new_critical_basis_points": 0,
    "require_all_categories": true,
    "minimum_category_cases": 1
  },
  "confirmed": true
}
```

The strings for plan and captures above are placeholders; the actual request
must contain JSON objects. Every original trial validation is rerun before
the two pins are checked. The policy is a diagnostic declaration, not a
precommitted statistical analysis plan or a replacement for existing
nomination, holdout and rollout controls. Defaults are the policy shown.

Offline CLI (exclusive new output file):

```sh
python -m shared.agent_procurement_case_robustness packet.json request.json robustness.json
```

HTTP: use the existing dashboard-session-only procurement operation
`POST /v1/process-catalog/procurement/case-robustness` with the usual saved
workflow/revision query pins and JSON-text document envelope. No credentials,
network source reads, agent execution or storage are added.

## Exact diagnostics

- Full enrolled population: paired match-rate difference, paired regression
  rate, and new-critical-attempt rate. Missing attempts retain their existing
  deterministic lower/upper bounds.
- Distinct-case all-repeat match rate: the lower endpoint counts cases whose
  every repeat is known to match; the upper counts cases with no known
  failed match. An unknown attempt does not establish success.
- Each frozen workflow category and each repeat block receives the same
  paired metrics and thresholds. Insufficient category coverage is unknown
  (an absent category may be explicitly not required).
- For each permitted number k of deleted whole cases, retain every repeat of
  every remaining case. The exact minimum lower numerator removes the k
  largest case-cluster lower sums. The exact maximum upper numerator removes
  the k smallest upper sums. All clusters have the same number of enrolled
  repeats, so the denominator is fixed. Sorting is sufficient; exhaustive
  subset enumeration is unnecessary. Ties use case ID order.
- Universal pass means every allowed subset meets the declared threshold
  under every completion of its missing-data bounds. Definite failure means
  at least one subset violates the threshold across its entire interval.
  For a minimum threshold, remove the k largest *upper* sums to find a
  definite-failure witness; for a maximum threshold remove the k smallest
  *lower* sums. Otherwise the result remains unknown. Different metrics and
  extrema may use different witness subsets.
- Every metric includes exact integer numerators and denominator. Policy
  comparisons use integer cross multiplication; the dashboard rounds
  presentation only. The full population is never discarded. Infeasible
  requested deletion depth makes coverage unknown rather than a pass.

Caps are inherited from frozen trials (50 distinct cases, 10 repeats,
10 candidates); maximum deletion depth is 10. The output is a deterministic
digest-bound, minimized report with opaque case IDs, not raw case inputs,
plans or model outputs.

## Interpretation and limitations

This is a finite enrolled population sensitivity analysis. It is not a
confidence interval, sample-size calculation, proof of representativeness,
causal benefit estimate, or evidence that independently repeated cases were
executed. Repeat labels do not establish randomized independent seeds.
It cannot prove safety on unobserved workflow categories or source states.
Do not use the stress subsets to cherry-pick or silently omit original cases.

Tests exercise the existing real trial fixtures, missing evidence,
tampered pins, strict policy, exact subset enumeration, exclusive offline
output, dashboard HTTP authorization, and the saved-trial browser journey.
