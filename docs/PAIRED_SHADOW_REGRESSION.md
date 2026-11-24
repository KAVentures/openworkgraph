# Paired shadow regression review (R17)

The exported Agents SDK project can now compare two previously captured
planner-result populations against the **same** explicitly reviewed workflow
specification and case suite.

This is useful when changing the planner model, prompt, model configuration or
other implementation detail while preserving the reviewed workflow. An
unchanged aggregate match count can conceal regressions on individual cases.
Do not compare different process revisions or different reviewed case suites
using this paired mode.

## Offline command

From a trusted extracted runtime project, with already captured
`openworkgraph.shadow-plan-results.v1` documents:

    python evaluate.py --cases reviewed-cases.json --baseline-results baseline-plans.json --results candidate-plans.json --output paired-review.json

The two results documents must each identify the exact specification and case
suite by digest and include each selected opaque case ID exactly once. The
checker recomputes the ordinary shadow evaluation for each document, rather
than accepting a previously asserted evaluation score. This is an offline
comparison; the baseline option cannot be combined with the live model option.

The new report includes:

- Exact workflow, suite, baseline results and candidate results digests.
- Baseline and candidate counts, and the net change in matching cases.
- Mutually exclusive paired transitions: unchanged match, improved, regressed
  and unchanged mismatch.
- Separate coverage and transitions for normal, exception and missing-facts
  cases; absent categories are explicitly listed.
- Newly introduced and total candidate critical review failures (expected
  clarification or unresolved review bypass).
- Opaque case IDs and input digests with baseline and candidate failure reasons.
  Inputs, planner text and routes are not included in this minimized report.

Exit 0 means every selected candidate case matched its reviewed expectation;
exit 2 means candidate review is required. Malformed, mismatched or incomplete
case populations refuse the comparison. Output files are never overwritten.

## Evidence boundaries

This report measures agreement with customer-reviewed expectations, not
independent ground truth, representative coverage, agent execution safety,
provider outcomes, or causal business value. An empty exception category is
a gap in evidence, not a successful exception test. The supplied results
documents are not independently authenticated to a particular model or run.
Record their provenance separately. A perfect paired result does not authorize
production actions. Use independent held-out cases, customer-approved tool
bindings, actual runtime safeguards and source-verifiable outcomes before
deploying.

This change preserves the existing single-result shadow comparison and the
separate explicitly consented current-source evaluation. It does not send
case data to a provider or modify the reviewed process.

Regression tests are part of the existing generated `test_evaluation.py`
suite: case swaps at equal aggregate scores, newly introduced review bypasses,
missing categories, exact digest binding, offline CLI and failure exit status.

Related: [#370 R17](https://github.com/KAVentures/openworkgraph/issues/370).
