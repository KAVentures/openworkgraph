# Procedural context packs

OpenWorkGraph can compose the read-only procedural-memory views into one small context bundle for an AI agent.

The context pack is **observational evidence**, not an organizational policy, instruction, recommendation, or learned rule. It does not auto-inject itself into an agent prompt and it does not execute any workflow action.

## Why this exists

The procedural-memory API exposes separate views for:

- workflow families;
- structurally similar prior executions;
- repeated explicit failure sequences;
- observed next-step frequencies among positive examples;
- repeated human-approval request hotspots.

An agent can query those views individually. The context-pack endpoint provides a bounded convenience layer when the caller already knows the procedural `family_key` it is working within.

## REST API

`GET /v1/procedural-memory/context-pack`

The route uses the same ordinary local API read bearer as the other procedural-memory routes. The write-only agent-ingest credential cannot read it.

Important parameters:

- `family_key` — required privacy-safe procedural family identifier;
- `current_steps` — optional comma-separated structural step tokens already observed in the current workflow;
- `after_step` — optional structural step used to query observed next steps when no prefix is supplied;
- `limit` — canonical evidence rows considered; default 10,000 and hard-capped at 25,000 for this endpoint;
- `min_support` — repeated-pattern threshold, minimum two;
- `run_limit` — similar-run count, hard-capped at five;
- `section_limit` — failure/next-step/approval items per section, hard-capped at five;
- `max_steps_per_run` — hard-capped at 24;
- `max_evidence_refs_per_item` — hard-capped at four.

The response reports the applied budget and whether any section was truncated.

## MCP

The local MCP server exposes:

`get_procedural_context_pack`

It is a thin authenticated client of the REST endpoint and passes the result through the existing OpenWorkGraph untrusted-data/prompt-injection protection and MCP audit boundary.

The MCP tool applies the same hard caps before sending the query to the local API.

## Authority and policy semantics

Every context pack includes an explicit authority block:

```json
{
  "observational_only": true,
  "authoritative": false,
  "prescriptive": false,
  "policy_status": "not_provided",
  "policy_inferred": false,
  "requires_human_review_for_policy": true
}
```

This is deliberate. Repetition in observed work does not establish that the behavior is correct, required, authorized, compliant, or preferred by the organization.

Approval hotspots likewise remain observations. A repeated approval request is not converted into a rule saying approval is required.

Failure patterns remain repeated explicit `error`, `denied`, or `cancelled` evidence and make no causal claim.

Observed next steps remain frequencies among positive examples and are not recommendations.

## Outcome semantics

The pack inherits the procedural-memory outcome rules:

- a human visible completion anchor is `observed_completion`, never `success`;
- agent `success`, `error`, `denied`, or `cancelled` requires structural native evidence;
- unknown outcomes remain unknown;
- the context pack does not improve or reinterpret the underlying evidence.

## Privacy

The pack does not introduce new raw evidence fields. It is built from the privacy-minimized procedural-memory outputs and additionally bounds what is returned.

In particular:

- raw session/run/trace/span identifiers are not returned;
- explicit workflow IDs remain hashed;
- arbitrary custom tool names remain hashed;
- dynamic/unknown human surfaces remain hashed;
- evidence references are hashed and capped;
- prompt text, model responses, tool arguments/results, typed field values, clipboard contents, and hidden reasoning are not added.

The public route also inherits the recursive internal-ID stripping used by the procedural-memory API.

## Non-goals in this version

This version does **not**:

- infer company SOPs or policies;
- write a skill or `AGENTS.md` automatically;
- choose an action for an agent;
- auto-inject context into third-party agents;
- create a learned-state database;
- run a background learner;
- modify capture, adapters, Gateway behavior, or the agent execution path.

A future explicit policy/SOP layer should remain separately sourced and separately labeled so an agent can distinguish **what has been observed** from **what the organization has actually declared**.
