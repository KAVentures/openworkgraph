# Agent evidence contract

OpenWorkGraph can represent AI agents as first-class participants in the same work graph as humans and systems.

This document defines the vendor-neutral structural contract used by future agent adapters. It does **not** make any one AI provider, framework, or agent runtime authoritative.

## Design goal

Desktop and browser evidence already answer questions such as:

```text
human -> email -> CRM -> spreadsheet -> response
```

Agent evidence should extend the same chronology:

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

No database migration is required merely to represent agent execution.

## Canonical operations

The first contract recognizes:

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

Agent execution is frequently a tree rather than a flat sequence. Adapters should preserve, when available:

```text
run_id
trace_id
span_id
parent_span_id
workflow_id
```

`workflow_id` is the cross-actor correlation hook. It can eventually connect a human desktop/browser session, one or more agent traces, CI/system activity, and later human review into a single workflow without pretending that their native session IDs are the same thing.

## Observation level

Every agent event states how directly OpenWorkGraph observed it:

- `native_trace` — emitted by the agent/runtime itself
- `instrumented_tools` — structural tool execution observed through hooks/instrumentation
- `mcp_only` — only activity traversing an MCP boundary is visible
- `os_observed` — inferred from ordinary desktop/browser observation
- `outcome_only` — only externally visible effects are known

This prevents OpenWorkGraph from implying that a closed agent's complete execution was observed when only part of it was visible.

## Tool categories

The first structural categories are:

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

The contract rejects content-bearing fields such as prompts, model responses, messages, chain-of-thought, tool arguments and tool results. It records privacy flags declaring that those contents were not captured.

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

This mirrors OpenWorkGraph's existing principle of retaining useful workflow evidence while minimizing unnecessary sensitive content.

## Adapter architecture

Future adapters should translate native telemetry before persistence:

```text
agent runtime / trace source
          |
          v
runtime-specific adapter
          |
          v
shared.agent_evidence.agent_event_to_evidence(...)
          |
          v
canonical OpenWorkGraph event
          |
          +--> local evidence store
          +--> exports / search / MCP
          +--> optional customer-hosted Gateway
```

The next implementation layer should add an authenticated local ingestion route that accepts only this contract. OpenTelemetry/GenAI traces are a natural first generic adapter after that. Provider-specific adapters should come later and should not alter the canonical contract unless a genuinely portable requirement is discovered.

## Compatibility rule

Adding agent evidence must not change existing desktop/browser capture semantics. A deployment that never emits agent events should behave exactly as before.
