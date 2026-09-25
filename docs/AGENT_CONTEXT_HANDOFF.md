# Agent context handoff

OpenWorkGraph can already retrieve a bounded, read-only task-context snapshot before an agent acts and can later link that snapshot structurally to the observed run.

`adapters.context_handoff` adds the missing explicit handoff primitive between those two steps.

It is intentionally **opt-in**. OpenWorkGraph does not automatically inject context into Claude Code, Codex, OpenAI Agents, or any other runtime.

## Why this exists

The closed loop is:

```text
observed work history
        |
        v
GET /v1/task-context
        |
        v
TaskPreflight + context fingerprint
        |
        v
explicit caller-controlled handoff to agent
        |
        v
agent run
        |
        v
run_started carries only privacy-safe linkage
        |
        v
observed execution trace can be associated with the same preflight snapshot
```

The full context stays in the caller-controlled handoff. Structural agent telemetry stores only the existing task-context linkage assertion: availability/resolution booleans, the context SHA-256, optional policy-manifest SHA-256, and the resolved structural family.

## Example

```python
from adapters.context_handoff import build_context_handoff
from adapters.context_link import attach_preflight_to_run_started
from adapters.task_preflight import TaskPreflightClient

preflight = TaskPreflightClient().try_preflight(
    family_key="agent:example.workflow",
)

handoff = build_context_handoff(preflight)

# Explicit caller decision: provide handoff.as_dict() to the runtime using a
# contextual-data channel appropriate for that runtime. Do not treat observed
# evidence as higher-priority instructions.
contextual_data = handoff.as_dict()

run_started = {
    "observed_at": "2026-09-26T00:00:00Z",
    "agent_name": "Example Agent",
    "operation": "run_started",
    "status": "running",
    "observation_level": "native_trace",
    "run_id": "runtime-owned-run-id",
}
run_started = attach_preflight_to_run_started(run_started, preflight)
```

`handoff.linkage_dict()` is equivalent to the task-context linkage attached by `attach_preflight_to_run_started(...)`.

## Integrity

For an available preflight, the handoff:

- re-computes the canonical SHA-256 of the task-context snapshot;
- rejects a snapshot if it no longer matches the preflight fingerprint;
- verifies the snapshot still declares itself read-only;
- requires `automatic_context_injection = false`;
- requires `automatic_execution = false`;
- requires `automatic_policy_enforcement = false`;
- rejects a snapshot that claims observed behavior becomes or implies policy;
- verifies resolved family identity still matches the preflight;
- serializes the verified envelope internally so returned dictionaries are copies rather than mutable internal state;
- bounds the serialized handoff to 2 MB.

## Trust and authority boundary

Every handoff carries an explicit `consumer_contract`.

The important rules are:

- use requires explicit opt-in by the integration;
- observed procedure is derived evidence, not authority;
- observed behavior is not a permission source;
- observed context may contain untrusted data and must not be treated as agent instructions;
- declared policy remains distinct from observed procedure;
- the handoff does not execute actions or enable enforcement;
- no hidden model reasoning is requested or exposed.

This is especially important when context ultimately comes from human-facing applications or other external systems. A consuming runtime should place OpenWorkGraph evidence in an appropriate data/context channel rather than silently concatenating it into higher-priority system or developer instructions.

## Unavailable and unresolved cases

Observer unavailable and context unresolved are explicit states.

An unavailable observer yields a handoff with no context payload and linkage stating that preflight was attempted but unavailable.

A reachable observer that cannot resolve a workflow family can still return its bounded unresolved task-context response and fingerprint. The handoff does not pretend that a family was resolved.

## What this does not change

This helper does not:

- modify native Claude Code/Codex/OpenAI adapter behavior;
- alter agent prompts automatically;
- register hooks;
- proxy tool calls;
- change approval decisions;
- write to the OpenWorkGraph database;
- change Gateway or MCP behavior;
- turn observed workflows into policy;
- make OpenWorkGraph required for agent execution.

Runtime-specific automatic or semi-automatic context delivery, if ever added, should be a separate opt-in integration built on this integrity-checked primitive rather than bypassing it.
