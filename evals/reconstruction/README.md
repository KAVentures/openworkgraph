# Workflow reconstruction evaluation

> Secondary evidence-sufficiency diagnostic; primary real-agent study:
> [../README.md](../README.md).


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

fixtures.py is the committed answer-key generator for 54 deterministic synthetic
cases spanning 34 independent scenario families. The original ten families retain structural
variants for regression coverage; twenty-four additional families use distinct topologies.
All synthetic resource, tab, transfer and page-path identifiers are deterministic opaque
tokens: equality relationships remain visible, but fixture roles such as noise/reference/
distractor are never encoded in IDs. Generated JSONL is intentionally not committed, which keeps the repository
small while making the corpus exactly reproducible.

Synthetic cases are engineering tests, not evidence of product value. The corpus deliberately
includes same-tab multiplexing, missing resource references, 4–5 concurrent workflows,
abandoned work, near-duplicate resources, ambiguous interruptions, multiple independent
uncertainty families, and a 50+ event session. A deterministic roughly-20% holdout can be
exported with `prepare --split holdout`; use `--split development` while iterating. Splitting
is by independent family, so structural variants of one family can never appear on opposite
sides of the holdout boundary. Because the answer-key generator is open source, this
is a procedural holdout rather than a secret test set: do not inspect/tune against holdout
predictions during development.

After the synthetic benchmark is stable, run the same protocol on 20-30 manually annotated
real sessions. Those sessions, not synthetic scores, answer the product question.

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

### One-command external runner

For a genuinely blind model run, use the external runner. The request contains only
`case_id`, `presented_evidence`, and the reconstruction instruction; it never sends
`ground_truth` or `source_events`.

Direct OpenAI-compatible endpoint:

    export OPENAI_API_KEY=...
    python -m evals.reconstruction.run_external --model <exact-model-id>

For development/holdout discipline, add `--split development` while iterating and run
`--split holdout` only for the final blind check. Split-specific artifacts use distinct
filenames, so they do not overwrite one another.

Vercel AI Gateway (also OpenAI-compatible):

    export AI_GATEWAY_API_KEY=...
    python -m evals.reconstruction.run_external \
      --base-url https://ai-gateway.vercel.sh/v1 \
      --api-key-env AI_GATEWAY_API_KEY \
      --model <provider/model-id>

Use an exact currently supported model ID from the provider/gateway model catalog.
Do not commit credentials. Temperature is omitted by default for compatibility with
reasoning models; pass `--temperature` only when the selected model supports it.

The runner writes ignored local artifacts under `evals/reconstruction/results/`:
predictions JSONL, a scored report, and run metadata including revision, model,
usage metadata returned by the endpoint, and elapsed time. `--resume` continues a
partially completed run without re-paying for completed cases.

## Stage B: real OWG/MCP path

An executable blind replay now exercises the shipped connector policy, Gateway
store, signed OAuth JWT verification/JWKS, public MCP redaction and pagination:

    python -m evals.reconstruction.replay --detail rich --output /tmp/hosted-rich.jsonl
    python -m evals.reconstruction.replay --detail compact --output /tmp/hosted-compact.jsonl

Every case uses a separate disposable OAuth identity. Temporary credentials/database
are deleted afterward; no ambient production OWG endpoint is used. Output contains
only blind presented evidence plus measured replay counts, never source events or truth.
Chronology is shifted into the current retention window while preserving time gaps.
Compact and rich responses intentionally differ; compare them as separate conditions.
This starts at synthetic stored-event input and does not exercise a real OS observer.
Browser runtime tests and the local-text-to-cloud OAuth regression cover capture boundaries.

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
| Hidden-rule uncertainty recall | >= 0.90 |
| Unnecessary uncertainty rate | <= 0.10 |

The scorer also reports ordering accuracy, automation-relevance precision/recall
and UI-mechanic precision/recall. It reports both historical case-level metrics and a
family-macro view. The release gate uses the family-macro metrics so the three variants
in each of families 01-10 do not receive triple the weight of independent single-case
families. Hidden-rule uncertainty is likewise macro-averaged across uncertainty families.

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

## Version 2 fidelity and observability

Fixtures now call `shared.evidence.rich_evidence_row` directly, use production-valid
resource references with hosts, use conservative action confidence and follow canonical
timestamp order. Every one of the 54 cases is replayed through the hosted MCP in tests.
These changes invalidate direct comparison with old fixture scores; record the commit
and `benchmark_contract_version` when publishing results.

Generic cross-resource labels without an observed transfer are conservatively flagged
as ambiguous membership. Hidden author intent is retained as a diagnostic partition,
but is not a certainty gate. Assignment thresholds use only the nonflagged membership
families; uncertainty thresholds still cover all families. This is a synthetic
observability screen, not independent human adjudication or proof that every unflagged
case is identifiable. `uncertainty` separates membership, business rule and persisted
outcome; a UI click alone cannot prove a persisted outcome. Legacy boolean outputs
remain supported, with less diagnostic resolution.

Actual cross-workflow mixing is measured separately from pure fragmentation. Fragmentation
still lowers one-to-one assignment F1 and workflow-count accuracy. Unknown event IDs lower
precision and are reported explicitly. The matching algorithm uses bounded-family dynamic
programming rather than factorial permutations of predicted fragments.

Paid/independent model trials and verified real-work episodes remain necessary to
establish usefulness. Passing transport tests, oracle scores or synthetic thresholds
cannot substitute for those measurements.
