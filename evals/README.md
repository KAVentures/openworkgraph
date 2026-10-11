# OWG evaluation: one product-value question

**Does an independent AI complete a user's goal better with OWG than with its
normal connectors alone?** This is the single primary evaluation. Older
benchmarks are secondary diagnostics, not separate claims of product efficacy.
Ordinary pytest/CI remains the protection for privacy, capture, OAuth, MCP
contracts, retention and installer functionality.

## Main study: three paired arms

Freeze 20–30 independently verified real interrupted-work episodes and source
system states before evaluating. Include ambiguous goals, old work, relevant
agent runs, interleaved resources, false matches, missing history and cases
where OWG should not be called. Do not use real private user content in the
synthetic public harness.

In fresh, independent model sessions, keep the **same model, goal, sampling,
source-system connectors, source-state snapshot, tool budget and grading
criteria**. Randomize conditions. Only the available OWG interface differs:

- `control`: normal agent and source-system connectors, **no OWG**.
- `raw`: same setup plus OWG's query-free raw chronological trace only.
- `goal`: same setup plus `get_goal_context` and its query-free raw drilldown.

Do **not** insert a curated evidence summary, route the model to a named tool,
preload answer keys or give the OWG condition a stronger prompt. If your AI
client cannot limit MCP tools per arm, label the comparison **not comparable**.
The existing hosted-context CLI runner is a historical **two-arm routing test**,
not an automated implementation of this three-arm study.

Record actual model/client transport traces, OWG calls and returned IDs.
Independently and blindly review completed turns against frozen source-system
state: selected correct/wrong resource IDs, agent-run retrieval, clarification
questions, unsupported assumptions, task completion and consequential mistakes.
Incomplete/authentication-blocked requests are unmeasured, never treated as
failed or successful agent turns.

## One scoring entry point, reusing continuity metrics

Use the existing episode truth schema in
[`continuity/README.md`](continuity/README.md), augmented with
`real_observed_work: true` and `ground_truth_verified_by: reviewer-id`
**only after independent verification**.

Each completed arm's JSONL record must contain:

```json
{
  "episode_id": "e001",
  "recorded_model_completion": true,
  "model_id": "verified-model-id",
  "source_state_id": "frozen-source-e001",
  "independent_review": {
    "reviewer_id": "independent-human",
    "selected_resources": ["opaque-file-a"],
    "selected_agent_runs": [],
    "clarification_questions": 0,
    "wrong_assumptions": 0,
    "continuation_success": false,
    "consequential_wrong_action": false
  }
}
```

This is a **schema example, not a measured experiment**. Score with:

```bash
python -m evals.product_value \
  --episodes /private/episodes.jsonl \
  --control /private/control-reviewed.jsonl \
  --raw /private/raw-reviewed.jsonl \
  --goal /private/goal-reviewed.jsonl \
  --out /private/report.json
```

The scorer does **not** run models or verify claimed reviewer independence.
It rejects unmatched cases, incomplete model turns, absent adjudication and
mismatched paired model/source snapshots; fewer than 20 verified real episodes
produce `pilot_only`. It compares goal vs no OWG (main), raw vs no OWG, and
goal vs raw (diagnosis). Better recall never outweighs higher wrong-resource
rates, unsupported assumptions or consequential mistakes. Thresholds are
engineering stop-losses, not statistical significance.

## Keep these diagnostics, run only when relevant

- `evals/reconstruction/`: capture/evidence sufficiency, grouping ambiguity.
- `evals/hosted_context/`: actual MCP invocation, discovery and raw fallback.
- `evals/continuity/`: old A/B scorer reused by the primary analysis.
- `evals/automation_interpretation_cases.json`: narrow automation-advice audit.
- `server/brief_evaluation.py`: optional in-product brief holdout; leave off
  unless the user explicitly enables it.

Paid AI runs and private trace archives do **not** belong in normal CI or
distributed app packages. The October 2026 hosted CLI attempts had zero
completed model turns; see [historical results](hosted_context/results/HISTORY.md).
OWG is the evidence layer: agent autonomy and permissions are **not** OWG features.
