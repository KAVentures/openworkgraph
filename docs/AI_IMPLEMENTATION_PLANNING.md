# Pre-implementation planning for a reviewed proposal batch (P0 #1663)

Compare candidate scenarios before committing to implementation. This extends
the existing multi-proposal review (#1688/#1689), not the existing saved-idea
portfolio optimizer or post-test operating-cost evaluator. The external AI and
the reviewer supply business judgments; OWG performs exact arithmetic and
maintains evidence lineage. No new ranking or solver, model calls, writes to the
portfolio, permissions, or execution.

## Prepare a complete planning worklist

Retain the original evidence files, returned proposal array, original packet
pin and independently retained batch-review pin. Specify one currency for
all assumptions (no conversions are performed).

```sh
python -m shared.ai_implementation_planning worklist \
  --source original-evidence.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json --batch-review-sha256 EXACT_REVIEW_PIN \
  --currency SEK --output planning-worklist.json --confirm-local
```

The worklist contains each distinct structurally reviewed proposal index and
digest, initially unassessed. Do not remove any row or change the identifiers.
Rejected and duplicate items remain in the final report. Leave an assessment
null if not reviewed. For a reviewed candidate, replace its assessment with
a declaration such as the following *illustrative assumptions only*:

```json
{
  "ranges": {
    "cases_per_month": ["60", "120"],
    "eligible_share": ["0.5", "1"],
    "net_human_minutes_saved_per_eligible_case": ["-6", "12"],
    "human_capacity_value_per_hour": ["100", "200"],
    "incremental_operating_cost_per_month": ["10", "100"],
    "one_time_implementation_cost": ["100", "200"]
  },
  "feasibility": "needs_review",
  "risk": "unknown",
  "depends_on_item_indexes": [],
  "basis_note": "Hypothetical bounds; obtain representative workload and cost measurements before adopting."
}
```

Ranges are exact decimal strings, low then high, or null if unknown.
Net human minutes must include review, supervision, exception handling,
fallback, rework and escalation, and may be negative. Include all
incremental ongoing AI, tools, infrastructure, and maintenance costs in the
operating range; avoid counting the same cost as both person-time and direct
expense. First-year figures assume twelve identical operating months, full
declared eligibility and the stated upfront implementation cost, not a rollout
schedule. Capacity valuation is not cash flow. Feasibility and risk are
human-reviewed declarations, not verified permissions, safety or readiness.
Use plausible, needs_review, blocked, or unknown for feasibility; low, medium,
high, or unknown for risk. Dependencies use zero-based distinct reviewed item
indexes and must be acyclic. The basis note should explain input provenance,
uncertainty, quality/adoption requirements and external dependencies.

After independent human review, set confirm_human_reviewed_assumptions to true.

```sh
python -m shared.ai_implementation_planning compare \
  --source original-evidence.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --proposals returned-proposals.json --batch-review-sha256 EXACT_REVIEW_PIN \
  --plan planning-worklist.json --output planning-review.json --confirm-local
```

## Interpretation

The report recomputes the original batch and verifies all proposal pins,
preserves every item disposition and unassessed candidate, and gives exact
rational lower/upper bounds for modeled net human hours, capacity value,
capacity value less incremental operating cost, and first-year capacity value
less incremental costs. Values are fraction strings (e.g. 1/60), not
probabilities or measured forecasts. Unknown inputs propagate to dependent
metrics. Each scenario is conditional and independent; no portfolio total is
computed. Shared scoped event citations flag potential overlaps, but disjoint
citations do not establish disjoint work, budgets, or resources. Missing or
non-plausible dependency assessments are visible; this does not establish
dependency readiness or produce a schedule. The report retains original coverage
gaps and exact input/report pins.

Review the output with the external AI and decision-makers, then use the
existing individual idea assessment and planning workflow, with separate
approval and the existing downstream portfolio solver. Do not adopt modeled
benefits as actual savings or as evidence that an AI system meets its
quality, safety, and operational requirements. The separate retained
outcome/operating-cost evaluation is necessary after testing.

The same planning workflow is available in the local implementation dashboard after reviewing a proposal batch. Expand **Compare implementation economics and dependencies**, enter the currency, prepare a worklist, and enable the candidates you wish to assess. Complete both bounds or leave both blank for unknown. Use displayed one-based item numbers for dependencies; the editor converts these to the original zero-based indexes. Confirm the assumptions explicitly, compare scenarios, and download the exact reviewed assumptions and report. Source, budget, batch or currency changes clear the worklist; editing assumptions clears the result and confirmation. Unassessed and invalid candidates remain visible. This does not save assessments or rank the portfolio. Mapping reviewed assumptions to saved assessments remains follow-up. The CLI remains available.
No live-data queries or external requests occur; outputs are exclusive
private bounded files.

## Validation

```sh
python -m pytest -q tests/test_ai_implementation_review.py tests/test_ai_implementation_batch.py tests/test_ai_implementation_planning.py
```
