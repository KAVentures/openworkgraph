# Agent execution traces

OpenWorkGraph can expose a provider-neutral, privacy-safe structural trace of observed agent runs through:

`GET /v1/agent-execution-traces`

The purpose is to show **how an agent worked**, not only whether a run ended successfully. The view is derived from the same canonical agent evidence already accepted from Claude Code hooks, Codex OTLP, the OpenAI Agents SDK adapter, generic OTLP and custom integrations.

## What a trace can show

For each observed execution, the response can include:

- ordered lifecycle operations such as run start/end, model calls, tool calls, handoffs, approvals and errors;
- observed status and duration;
- provider/framework/agent descriptors;
- model name when structurally reported;
- structural tool name/category;
- bounded token-usage counters when reported;
- opaque span and parent-span references so parent/child execution structure can be reconstructed;
- the existing privacy-safe structural step tokens;
- conservative derived workflow family and outcome;
- whether run-start and run-finish boundaries were actually observed;
- privacy-safe task-context and shadow-enforcement flags when those assertions were attached.

## What it never returns

The endpoint does not expose native run, trace, span or workflow identifiers. It does not expose prompts, model-response content, tool arguments, tool results, chain-of-thought/reasoning, context hashes or policy-manifest hashes.

Event IDs and span relationships are represented only through local opaque references.

## Completeness is explicit

Different agent runtimes expose different lifecycle surfaces. A trace therefore reports:

- `run_start_observed`;
- `run_finish_observed`;
- `complete_boundary_observed`;
- total event count versus returned event count;
- whether the bounded event list was truncated.

A partial observation is never silently presented as a complete run.

## Authorization

The endpoint uses the normal authenticated API/dashboard read credential. The write-only agent-ingest credential cannot read traces.

## Query parameters

- `family_key` — optional validated structural workflow-family filter;
- `execution_id` — optional opaque execution identifier previously returned by this endpoint;
- `since` — bound canonical evidence by time;
- `evidence_limit` — bounded evidence read;
- `limit` — maximum executions returned;
- `max_events_per_execution` — maximum ordered events returned per execution.

## Interpretation boundary

This view is derived structural evidence. It does not claim that every internal model thought or runtime action was observable, and it does not infer hidden reasoning. It reports only the lifecycle signals that the instrumented runtime actually exposed.
