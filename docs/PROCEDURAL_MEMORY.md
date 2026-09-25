# Procedural memory

OpenWorkGraph procedural memory is a **read-only, regeneratable view over canonical workflow evidence**. It summarizes how work has repeatedly been observed to proceed across people and supported AI agents.

It is deliberately different from ordinary agent memory:

- context answers: **what is happening now?**
- factual memory answers: **what facts were retained?**
- procedural memory answers: **what structural paths have repeatedly been observed for this kind of work?**

The first implementation does not create a new learned-state database, run a background learner, or ask an LLM to decide what a procedure is. Every result can be regenerated from the canonical OpenWorkGraph `events` evidence.

## Available views

The authenticated local API exposes:

```text
GET /v1/procedural-memory
GET /v1/procedural-memory/similar-runs
GET /v1/procedural-memory/failure-patterns
GET /v1/procedural-memory/next-steps
GET /v1/procedural-memory/approval-patterns
```

The local MCP surface exposes corresponding tools:

```text
get_procedural_memory
get_similar_runs
get_failure_patterns
get_next_likely_steps
get_approval_patterns
```

MCP reads still use the existing AI-access control, authenticated local API transport, audit logging, and prompt-injection protection.

## What counts as an execution

### Human work

Human executions come from the existing evidence-backed task segmentation layer. Procedural memory does **not** reuse arbitrary UI labels as procedure steps.

It projects human work into a coarse structural vocabulary such as:

```text
surface:gmail
action:click
action:edit
action:submit
surface:github
```

Known generic surfaces may remain readable. Unknown/dynamic surface identities are represented by stable hashes so repeated structure can still be matched without echoing arbitrary window/page/customer text.

A strong human completion anchor is reported as:

```text
observed_completion
```

It is intentionally **not called `success`**. OpenWorkGraph may observe that a person sent/submitted/completed a visible interaction without knowing whether the underlying business outcome was correct.

### Agent work

Supported agent traces are grouped into runs and projected into structural steps such as:

```text
model_call
tool:search:tool:<stable-hash>
approval_request
approval_received:success
tool:code:tool:<stable-hash>
handoff
```

Tool names are hashed in this memory layer. The coarse category remains readable and the hash preserves stable same-tool matching across runs.

Agent `success`, `error`, `denied`, or `cancelled` is used only when the structural agent evidence explicitly reports that outcome. A run with no terminal evidence remains:

```text
unknown
```

Unknown is never silently promoted to success or failure.

## Procedural families

Executions are grouped into families using the strongest safe identity available:

1. selected stable human task families already produced by OpenWorkGraph;
2. an explicit agent `workflow_id`, stored only as a hash in procedural memory;
3. otherwise a structural signature derived from coarse steps/tool categories.

A family is an observed grouping, not a formal SOP.

The overview reports evidence quantities such as:

- execution count;
- positive-example count;
- observed human completion count;
- explicit agent success count;
- explicit agent failure count;
- unknown outcome count;
- dominant observed structural sequence and support.

`reusable_candidate=true` means only that the family has at least two positive examples. It does not mean the sequence is approved, optimal, compliant, or safe to automate.

## Similar runs

`get_similar_runs` retrieves prior executions from the same family and can optionally compare a caller-provided structural step prefix.

Similarity is deterministic and based on structural sequence overlap/prefix agreement. There are no embeddings or LLM judgments in this first version.

Returned execution/evidence IDs are local stable hashes. Raw session, run, trace, and span identifiers are not part of the public procedural-memory contract.

## Failure patterns

Failure patterns are deliberately narrow.

A pattern can only be created from repeated explicit agent evidence with statuses such as:

```text
error
denied
cancelled
```

The default minimum support is two executions.

A returned pattern means:

> this structural sequence was repeatedly observed together with an explicit failure status.

It does **not** mean:

- the preceding step caused the failure;
- the workflow is intrinsically wrong;
- the model/tool/user is at fault;
- the pattern should automatically be avoided.

The API therefore labels these as derived, non-authoritative evidence and explicitly makes no causal claim.

## Next observed steps

`get_next_likely_steps` is intentionally empirical rather than prescriptive.

For a family and optional structural prefix/preceding step, it reports next steps repeatedly observed among **positive examples only**, together with:

```text
support
opportunities
observed_fraction
```

The output explicitly carries:

```text
prescriptive = false
```

A high observed fraction is not automatically a recommendation. It can reflect convention, legacy process, local tooling, or an inefficient habit.

## Approval patterns

Approval-pattern retrieval looks for repeated native `human_approval_requested` evidence in agent runs and reports the structural point immediately before the request.

It may report observed decision-status counts when an adapter has explicit provenance for the human decision.

It never converts repetition into organizational policy. Every result carries:

```text
normative_requirement = false
```

and the response states that no normative approval requirement was inferred.

A company policy saying that a supervisor **must** approve a payment is a different source of truth and should eventually be attached as explicit policy/procedure data rather than inferred from telemetry alone.

## Privacy boundary

Procedural memory intentionally minimizes identifiers and content further than raw local evidence:

- raw session IDs are not returned;
- raw agent run/trace/span IDs are not returned;
- explicit workflow IDs are hashed;
- evidence event IDs are represented by stable hashes;
- custom tool names are hashed;
- unknown surfaces/families are hashed;
- arbitrary UI labels are not procedure steps;
- prompt, model-response, tool-argument/result and chain-of-thought content are not introduced by this layer;
- instruction-like custom names are hashed before they can become memory tokens.

The REST layer additionally strips internal session/run/trace/span identifier keys before returning a response.

MCP applies the repository's existing untrusted-observed-data filter again before the result reaches a model.

## Authentication

Procedural memory is a read surface and requires the ordinary local API read credential.

The least-privilege `.agent_ingest_token` remains **write-only** and cannot query procedural memory. An instrumented agent therefore does not gain access to organizational history merely because it can submit its telemetry.

## Current limitations

This first slice intentionally does not:

- persist learned procedures independently of source evidence;
- use embeddings or semantic task descriptions;
- decide which workflow is objectively best;
- infer causality from failure correlations;
- infer company policy from approvals;
- promote human completion to business success;
- automatically execute retrieved procedures;
- modify agent prompts or behavior.

Those capabilities should be added separately, with explicit policy/provenance and evaluation, after the read-only evidence layer has proven stable.
