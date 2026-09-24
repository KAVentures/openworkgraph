# Agent evidence and ingestion

OpenWorkGraph represents AI agents as first-class participants in the same work graph as humans and systems.

This document defines the vendor-neutral structural contract, the authenticated local ingestion paths, OpenTelemetry mapping, human-to-agent correlation, and the privacy boundary. No AI provider, framework, or agent runtime is authoritative.

## Design goal

Desktop and browser evidence already answer questions such as:

```text
human -> email -> CRM -> spreadsheet -> response
```

Agent evidence extends the same chronology:

```text
human -> agent request
      -> agent run
         -> model call
         -> tool call
         -> tool call
         -> human approval
         -> tool call
      -> agent completion
human -> review -> downstream action
```

The canonical record remains observed evidence. Higher-level interpretations can be regenerated later.

## Existing storage shape is retained

Agent activity maps into the existing canonical event shape:

```text
event_id
observed_at
schema_version
organization_id
actor_id
device_id
sensor_id
source
session_id
app
window_title
event_type
duration_seconds
screenshot_path
metadata
```

For agent events:

```text
source = agent
metadata.actor_kind = agent
```

No database migration is required. The existing raw evidence, normalized evidence, context layer, export/query path and optional Gateway synchronization all continue to use the same canonical store.

## Local authenticated ingestion

Two machine-write routes are available on the normal secure local server:

```text
POST /v1/agent-events
POST /v1/agent-events/otel
```

Both require the same local collector bearer credential used for trusted machine capture. A browser or dashboard session is not an agent-write credential.

Direct structural batches use:

```json
{
  "events": [
    {
      "event_id": "optional-stable-id",
      "observed_at": "2026-09-25T00:00:00Z",
      "agent_name": "Example Agent",
      "operation": "tool_call",
      "status": "success",
      "observation_level": "native_trace",
      "run_id": "run-1",
      "trace_id": "trace-1",
      "span_id": "span-2",
      "parent_span_id": "span-1",
      "workflow_id": "workflow-9",
      "tool_name": "repository_search",
      "tool_category": "search",
      "duration_seconds": 0.4
    }
  ]
}
```

The complete batch is validated before the first database write. A malformed or content-bearing event rejects the batch rather than leaving a partially accepted tail/head.

Current safety limits are 500 direct events, 1,000 OpenTelemetry spans, and 2 MB of JSON per request.

## Canonical operations

The contract recognizes:

- `run_started`
- `run_finished`
- `model_call`
- `tool_call`
- `handoff`
- `human_approval_requested`
- `human_approval_received`
- `error`

Provider-specific events should be translated into these structural operations rather than introducing a new event vocabulary for every runtime.

## Trace hierarchy

Agent execution is frequently a tree rather than a flat sequence. Adapters preserve, when available:

```text
run_id
trace_id
span_id
parent_span_id
workflow_id
trigger_event_id
```

`workflow_id` is the cross-actor correlation hook. It can connect a human desktop/browser session, one or more agent traces, CI/system activity, and later human review without pretending that their native session IDs are the same thing.

`trigger_event_id` is an optional explicit provenance link back to the observed human/system event that launched the run. Explicit links take precedence over temporal inference.

## Human-to-agent workflow view

The authenticated read endpoint:

```text
GET /v1/agent-workflows
```

builds a derived index over the canonical evidence. It groups agent events into runs and, when possible, associates them with an earlier observed human agent-submit event.

Linking rules are deliberately conservative:

1. explicit `trigger_event_id`;
2. close temporal + matching agent surface/name;
3. very-close temporal fallback only;
4. otherwise the run remains `unlinked`.

Every returned link includes its method and confidence and is marked `authoritative: false`. The source events remain the evidence.

## Observation level

Every agent event states how directly OpenWorkGraph observed it:

- `native_trace` — emitted by the agent/runtime itself
- `instrumented_tools` — structural tool execution observed through hooks/instrumentation
- `mcp_only` — only activity traversing an MCP boundary is visible
- `os_observed` — inferred from ordinary desktop/browser observation
- `outcome_only` — only externally visible effects are known

This prevents OpenWorkGraph from implying that a closed agent's complete execution was observed when only part of it was visible.

## Tool categories

The structural categories are:

```text
filesystem
shell
browser
code
search
network
database
messaging
issue_tracker
deployment
mcp
other
none
```

Tool names can remain runtime-specific while category-level analytics remain portable.

## Content is deliberately excluded

The agent contract is structural telemetry, not a prompt logger.

Direct structural ingestion recursively rejects content-bearing fields such as prompts, model responses, messages, chain-of-thought/reasoning, tool arguments, and tool results.

OpenTelemetry ingestion uses an allowlist: only structural GenAI attributes needed for workflow evidence are read. Arbitrary span attributes, input/output messages, tool arguments, tool results, raw native events, and prompt/completion payloads are never copied into the canonical event.

Default structural fields can include:

- agent/runtime name;
- provider/framework/model identifier;
- operation and status;
- trace/span relationships;
- tool name and coarse category;
- duration;
- optional token counts;
- observation level;
- cross-workflow correlation ID.

Agent events also carry explicit privacy flags recording that prompt/model/tool-content/chain-of-thought payloads were not captured.

## OpenTelemetry / GenAI adapter

`POST /v1/agent-events/otel` accepts OTLP/HTTP JSON and maps known GenAI spans into the canonical agent contract.

Supported structural mappings currently include:

```text
invoke_agent / invoke_workflow -> run_started + run_finished
chat / generate_content / text_completion / embeddings -> model_call
execute_tool / retrieval -> tool_call
```

Unknown spans are ignored rather than guessed.

Optional OpenWorkGraph defaults can be supplied at the top level:

```json
{
  "openworkgraph": {
    "organization_id": "acme",
    "actor_id": "agent:researcher",
    "device_id": "worker-7",
    "agent_name": "Research Agent",
    "provider": "example",
    "framework": "internal-agent",
    "run_id": "run-42",
    "workflow_id": "workflow-9"
  },
  "resourceSpans": []
}
```

The adapter also recognizes structural custom span attributes `openworkgraph.workflow.id`, `openworkgraph.run.id`, `openworkgraph.actor.id`, and `openworkgraph.trigger_event_id` when present.

See `examples/agent_otel_json.py` for a minimal live local ingestion example.

## Adapter architecture

```text
agent runtime / trace source
          |
          v
runtime-specific or OpenTelemetry adapter
          |
          v
shared.agent_evidence.agent_event_to_evidence(...)
          |
          v
canonical OpenWorkGraph event
          |
          +--> local evidence store
          +--> normalized/context layers
          +--> exports / search / MCP
          +--> optional customer-hosted Gateway
```

Provider-specific adapters should remain thin translations into this contract. They should not alter the canonical vocabulary unless a genuinely portable requirement is discovered.

## Compatibility rule

Adding agent evidence must not change existing desktop/browser capture semantics. A deployment that never emits agent events behaves exactly as before.
