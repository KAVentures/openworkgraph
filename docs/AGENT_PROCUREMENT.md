# Open AI workforce procurement (R26)

Use your saved process export, reviewed shadow suite and minimized evaluation
reports to compare candidate agents independently of the runtime vendor. This
first offline workbench reuses the existing exact shadow report schema and
specification validator. It makes no model calls or writes to business systems.

## Run

Obtain the saved process JSON export and one minimized shadow report per
candidate from the existing generated evaluator (see PROCESS_SHADOW_EVALUATION.md).
All reports must identify the exact same specification, suite hash, case IDs,
input hashes and categories. Copy each report object into a private request:

```json
{
  "format": "openworkgraph.agent-procurement.v1",
  "baseline_candidate_id": "current",
  "currency": "USD",
  "gates": {
    "minimum_match_basis_points": 9000,
    "maximum_critical_cases": 0,
    "maximum_total_cost_microunits": 5000000,
    "maximum_p95_elapsed_ms": 60000,
    "require_all_categories": true,
    "prohibit_new_critical_cases": true,
    "prohibit_baseline_regressions": false
  },
  "candidates": [
    {
      "candidate_id": "current",
      "provider": "provider-A",
      "model": "model-A",
      "runtime": "cli",
      "report": {},
      "measurements": [
        {"case_id": "case_1", "cost_microunits": 10000, "elapsed_ms": 2000}
      ]
    },
    {
      "candidate_id": "alternative",
      "provider": "provider-B",
      "model": "model-B",
      "runtime": "mcp",
      "report": {},
      "measurements": [
        {"case_id": "case_1", "cost_microunits": null, "elapsed_ms": 1500}
      ]
    }
  ]
}
```

Replace empty report objects with actual reports and include one measurement
row per case; null means unknown, never zero. Cost microunits are millionths of
the chosen currency (USD 0.01 = 10,000); use a common all-in cost basis and
elapsed timing boundary. Candidate labels are bounded aliases and must not
contain sensitive information. The illustrative gates are user choices, not
OWG policy or evidence of adequate performance.

```sh
python -m evals.agent_procurement --process-export saved-process.json --request procurement.json --out comparison.json
```

The output contains explicit per-candidate gates, category coverage, per-case
regressions and critical failures, cost and nearest-rank p95 latency,
paired measurement deltas, and the nondominated set among fully measured
gate-passing candidates. Identical report envelopes are refused; shared result
hashes in different report envelopes are disclosed. No automatic winner is
selected. A failing or unknown gate does not authorize substitution.

Unknown cost or latency cannot be treated as zero; known cost is only a lower
bound, and incomplete telemetry excludes the candidate from the Pareto set.
A known partial cost exceeding the gate fails it even when the total is unknown.
Critical reasons are invalid/stale plan, invalid route and unresolved-review
bypass. Other mismatches remain visible; use strict per-case regression gates
where appropriate. No independence or representativeness is inferred from the
category labels. The report uses exact counts rather than uncertainty claims.

The comparison accepts 2–10 candidates, at most 50 cases per report and 8 MiB
per input. Output creation is exclusive. No report or case content is uploaded.
Keep inputs and outputs private; hashes identify content, not anonymization.
This first slice compares complete shadow reports, not failed attempts, verified
runtime identities, actual business outcomes, repeated stochastic performance or
independently captured provider usage. Those remain R26 acceptance work.
