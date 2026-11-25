
# Prospective repeated procurement trials

The companion CLI freezes the exact minimized reference report, candidate
aliases and configuration hashes, baseline, currency, acceptance gates,
repeat count (1–10) and seed before comparing results. It creates a deterministic
rotation of candidate positions for each case. The schedule is a plan, not
proof of the actual order or that a provider ran the declared configuration.

Use the existing generated shadow evaluator with the same exact reviewed
suite for each candidate/repeat. For undelivered case plans, insert null
placeholders in the captured-plan results so the evaluator can return its full
minimized report. A null placeholder receives invalid_or_stale_plan; this
ledger treats that row as non-delivery rather than a returned unsafe plan.
Never omit a failed case from a denominator.

```sh
python -m evals.agent_procurement_trials freeze --process-export process.json --design design.json --out plan.json
python -m evals.agent_procurement_trials review --process-export process.json --plan plan.json --captures captures.json --out review.json
```

Design shape: format openworkgraph.procurement-trial-design.v1,
reference_report (existing minimized report), baseline_candidate_id, candidates
(each candidate_id, provider, model, runtime, configuration_sha256), repeats,
schedule_seed, currency, gates, confirmed (true).
Required gate: minimum_match_basis_points (0–10000). Defaults require all
attempts recorded, no non-delivery, no critical attempts, no new critical
relative to baseline and all three shadow categories. Optional gate fields:
minimum_recorded_basis_points, maximum_non_delivery_basis_points,
maximum_critical_attempts, maximum_total_cost_microunits,
maximum_p95_elapsed_ms, prohibit_new_critical, require_all_categories.

Capture envelope:

```json
{
  "format": "openworkgraph.procurement-trial-captures.v1",
  "plan_sha256": "REPLACE_WITH_FROZEN_PLAN_SHA256",
  "captures": [{
    "candidate_id": "current",
    "repeat": 1,
    "configuration_sha256": "REPLACE_WITH_CANDIDATE_CONFIGURATION_SHA256",
    "report": null,
    "attempts": [{
      "case_id": "case_1",
      "status": "timeout",
      "cost_microunits": 1000,
      "elapsed_ms": 60000
    }]
  }]
}
```

Use a complete minimized report whenever at least one plan was delivered.
Statuses: completed, timeout, transport_failed, invalid_output, withdrawn.
Omitted slots are not_recorded. Every planned slot remains in the result,
including entirely absent candidate captures. Unknown monetary/time values
must be null, not zero. Include actual incurred failure costs and elapsed
time using a consistent measurement boundary. A repeated invocation belongs
to its next prespecified repeat, never a replacement for a failed slot.

The review reports deterministic all-enrolled match/non-delivery/critical
bounds, category and case repeatability, paired baseline difference and
regression bounds, all-attempt cost lower bounds, nearest-rank p95 latency
bounds, gates and complete-evidence Pareto review. No assumption of
independence or random missingness is made. No claimed statistical confidence
interval is derived from repeated observations of one case. Exact duplicate
report envelopes are refused; identical result hashes across different
captures are disclosed.

This is an offline first slice, not live execution or attested receipts.
No source/model calls or business writes occur. Real replay adapters and
independently captured provenance remain separate R26 work. Do not treat
review as deployment approval. Protect the reference and report hashes as
customer-controlled identifiers. Inputs are limited to 8 MiB each, at most
10 candidates, 50 cases and 10 repeats. Output files never overwrite.

## Local dashboard

The same catalog panel supports **Freeze a repeated trial plan** and **Review a frozen trial and captures**. Download the exact plan and later select it alongside the capture ledger. Planned and recorded attempt counts, gates, missing-data bounds and paginated paired evidence remain local. API requests are dashboard-session-only, scoped to the saved revision, strictly bounded and no-store. The raw file text is parsed server-side to preserve duplicate-key refusal; no report is retained. The production CLI is also available as python -m shared.agent_procurement_trials.
