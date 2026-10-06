# Workflow reconstruction evaluation

This benchmark tests a narrow product thesis:

> Is OpenWorkGraph's privacy-hardened observed evidence rich enough for a capable
> external AI to reconstruct real human work when several workflows are
> interleaved, without trusting OWG's inferred task labels?

It is separate from the continuity eval and the automation-interpretation eval.

## What it tests

Real work is not a sequence of clean task-sized blocks. People routinely do
A1 -> A2 -> B1 -> C1 -> A3 -> B2 -> A4. The benchmark therefore makes workflow
membership non-contiguous and includes:

- two tasks in Gmail or Salesforce at the same time;
- three interleaved workflows;
- long interruptions followed by resumption;
- shared resources used by different tasks;
- missing resource references;
- cross-app clipboard linkage;
- human and agent activity interleaved;
- a deliberately unobservable approval rule.

Ground truth is event-level. The model never receives workflow IDs, checkpoint
IDs, or the answer key.

## Corpus

fixtures.py is the committed answer-key generator for 30 deterministic synthetic
cases. Generated JSONL is intentionally not committed, which keeps the repository
small while making the corpus exactly reproducible.

Synthetic cases are engineering tests, not evidence of product value. After the
synthetic benchmark is stable, run the same protocol on 20-30 manually annotated
real sessions.

Validate the built-in corpus:

    python -m evals.reconstruction.fixtures

Export it when a runner needs standalone JSONL:

    python -m evals.reconstruction.fixtures --output /tmp/reconstruction-cases.jsonl

## Stage A: raw evidence sufficiency

This is the most important first test. It removes OWG's derived task/family
grouping from the experiment.

    python -m evals.reconstruction.prepare --output /tmp/reconstruction-prompts.jsonl

For every row, send the supplied system_instruction plus presented_evidence to
the model under test and save the returned JSON as one JSONL row. Never expose
ground_truth.

Score:

    python -m evals.reconstruction.scoring --predictions /tmp/model-predictions.jsonl

Record the exact model ID, client/version, settings and repository SHA with the
results.

## Stage B: real OWG/MCP path

Load each case's source_events into a clean OWG instance, then expose the
result through the real compact MCP server for the revision being evaluated.
The model should see the same AI-facing trace shape as presented_evidence, not
the source-only actor/device/sensor identifiers. Ask the same reconstruction
question, with canonical evidence as the authority.

Interpret the comparison this way:

- Stage A good, Stage B worse: retrieval/navigation/grouping is likely the bottleneck.
- Stage A poor: the observed evidence itself is probably missing signal.
- Stage B better without higher contamination: derived navigation is helping.

Paid model calls do not belong in ordinary CI. The model run is an external
evaluation step, like the existing automation-interpretation eval.

## Ablations

After a baseline model run, remove one evidence signal at a time:

    python -m evals.reconstruction.ablations \
      --mode resource_references \
      --output /tmp/no-resource-refs.jsonl

Available modes are resource_references, tab_context, semantic_actions,
safe_labels and clipboard_linkage.

A large quality drop after removing one signal tells us what capture work is
worth prioritizing.

## Prediction contract

Each prediction is one JSON object with:

- case_id
- workflows
- each workflow's event_ids and ordered_event_ids
- automation_relevant_event_ids
- ui_mechanic_event_ids
- unassigned_event_ids
- insufficient_evidence
- optional notes

Workflow names are not scored. Predicted workflows are matched to hidden ground
truth by maximum event overlap.

## Preregistered engineering thresholds

| Metric | Threshold |
| --- | ---: |
| Workflow assignment F1 | >= 0.90 |
| Cross-workflow contamination | <= 0.05 |
| Exact workflow-count accuracy | >= 0.90 |
| Meaningful-checkpoint recall | >= 0.90 |
| Unrelated-interruption rejection | >= 0.95 |
| Hidden-rule uncertainty accuracy | >= 0.90 |

The scorer also reports ordering accuracy, automation-relevance precision/recall
and UI-mechanic precision/recall.

These are engineering release thresholds, not claims of statistical
significance. A publishable study should add a larger preregistered real-work
sample, blinded adjudication, paired resampling/confidence intervals and
multiple frontier model families.

## Interpretation rule

Historical human behavior is evidence, not policy or permission. A model should
use observed work to identify useful checkpoints and information dependencies,
while preferring live authorized connectors/APIs over literal UI replay. A
missing historical payload does not prove an automation is impossible, and an
observed approval does not reveal the business rule that caused it unless that
rule is actually present in evidence.
