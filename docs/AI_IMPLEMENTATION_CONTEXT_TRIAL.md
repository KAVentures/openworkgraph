# Paired original/compact context experiment

Compare the existing original and lossless compact evidence representations
using the same selected authorized source files, exact original packet, model
identifier and requested settings. This is a local proposal-generation
experiment, not a workflow execution benchmark or a cost optimizer.

Use an operator-controlled, configured OpenAI-compatible IPv4 loopback
service. Verify its logging, egress and permissions: loopback does not prove
that inference is local. The command deliberately sends selected evidence
twice. Both arms are predeclared, even if the first fails; neither is a retry
or automatic fallback. Do not use the experiment on sensitive evidence
without appropriate authorization.

```sh
python -m shared.ai_implementation_context_trial \
  --source selected-workflow.json --model YOUR_LOCAL_MODEL_ID \
  --port 11434 --order original-first --confirm-two-requests \
  --out context-trial.json
```

Repeat --source for the same selected evidence bundles. The experiment
preflights the existing evidence contract and exact compact expansion;
non-shrinking compact evidence is refused before requesting inference.
The original packet must fit the configured context-byte budget. The
existing bounded adapter validates each returned proposal and citations.
Use the same settings across experiments and vary the explicit arm order
(original-first or compact-first) to investigate ordering/cache effects.
The CLI reserves a new private output file before making requests; discard
an incomplete output if the command fails.

The report retains both attempted arms and their validated proposals and
receipts when available, or a coarse failure class and elapsed time.
Original-minus-compact deltas include evidence and HTTP request bytes,
endpoint-reported token counts and adapter elapsed time. Positive deltas
mean the compact arm was smaller/faster for that metric in this pair.
If either receipt is unavailable, the paired deltas are unknown. Missing
token telemetry is unknown even with two valid proposals; failed requests
may incur spend. The report's digest pins its exact content. Reports include
model-generated text and may contain sensitive company information.

A structurally valid proposal is not an independently correct proposal.
Independently assess both outputs, ideally blind to representation, against
held-out cases, source outcomes and company-reviewed rules. Single paired
runs cannot control for cache, backend, order or stochastic effects.
No billed costs, token savings across workloads, independent model identity,
local inference location, quality equivalence or business outcomes are
verified by this experiment. No representation is selected for production.
Use the existing procurement/economic evaluation for actual business-case
decisions. No model or business workflow is installed or deployed.

## Validation

```sh
python -m pytest -q tests/test_ai_implementation_context_trial.py
```

Tests use real synthetic loopback HTTP calls, both orderings, failed arms,
missing usage, exact source/citation checks, preflight refusal and
exclusive file output. A synthetic provider is not a real model benchmark.
