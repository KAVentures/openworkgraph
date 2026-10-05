# Ambiguous continuity A/B

This evaluation decides whether OpenWorkGraph's remaining continuity thesis is
worth building around.

## Hypothesis

A frontier agent that already has its normal connectors and tools should recover
ambiguous interrupted work materially better when it also receives OpenWorkGraph's
privacy-minimal resource + agent continuity graph.

This is **not** an evaluation of workflow imitation, task mining, or whether OWG
can produce plausible labels.

## Study design

Build at least 20-30 real interrupted-work episodes from work the evaluator
actually performed. Freeze the ground truth before running either arm.

For each episode run the **same model, model settings, user prompt, connector
permissions and source-system state**:

- **Control:** normal agent context/connectors, no OWG continuity context.
- **OWG:** identical setup plus the OWG continuity context.

Examples of prompts:

- "Continue what I was doing before lunch."
- "Finish that Acme thing from yesterday."
- "Pick up where Claude stopped."
- "Open the documents I was using for that pricing issue."
- "What was left unfinished on Friday?"

Do not use the synthetic fixture below as evidence of product value; it exists
only to smoke-test the scorer.

## Ground-truth episode schema

Each JSONL row:

```json
{
  "episode_id": "e001",
  "prompt": "Continue the Acme thing from yesterday.",
  "correct_resources": ["gmail:thread:opaque-a", "salesforce:opp:opaque-b"],
  "correct_agent_runs": ["execution:opaque-c"],
  "distractor_resources": ["salesforce:opp:opaque-old"],
  "unfinished_state": "draft prepared, not sent"
}
```

Use opaque evaluation IDs. The scorer does not need real customer names or
content.

## Prediction schema

Each arm produces one JSONL row per episode:

```json
{
  "episode_id": "e001",
  "selected_resources": ["gmail:thread:opaque-a", "salesforce:opp:opaque-b"],
  "selected_agent_runs": ["execution:opaque-c"],
  "clarification_questions": 0,
  "wrong_assumptions": 0,
  "continuation_success": true,
  "consequential_wrong_action": false
}
```

A resource counts as selected when the agent materially relies on or opens it,
not merely when it appears in a long search-results list.

## Metrics

The scorer reports:

- correct-resource precision and recall;
- wrong-resource rate and wrong resources per episode;
- correct prior-agent-run recall;
- clarification questions per episode;
- wrong assumptions per episode;
- successful continuation rate;
- consequential wrong-action rate.

Wrong-resource selection is a first-class failure. A context layer that makes an
agent confidently open or act on the wrong object can be worse than no context.

## Pre-registered product decision

The default decision is deliberately demanding because OWG requires a local
observer and OS permissions.

Continue the continuity thesis only if OWG achieves **at least one** of:

1. +15 percentage points mean resource recall, with no increase in wrong-resource
   rate, wrong assumptions, or consequential wrong actions;
2. +15 percentage points successful continuation, with the same safety condition;
3. at least 0.5 fewer clarification questions per episode **and** at least +5
   percentage points resource recall, again without worse wrong-resource, wrong-assumption, or
   consequential-action rates.

Otherwise the result is `stop_or_pause_product_pivot`.

These thresholds are product thresholds, not claims of statistical significance.
For a publishable experiment, add confidence intervals, paired resampling, blinded
adjudication and a larger preregistered sample.

## Run

```bash
python -m evals.continuity.scoring \
  --episodes evals/continuity/episodes.jsonl \
  --control /path/to/control_predictions.jsonl \
  --owg /path/to/owg_predictions.jsonl \
  --output /tmp/continuity-results.json
```

Before using the result to make a product decision, inspect every false positive
and determine whether OWG's resource association caused it.
