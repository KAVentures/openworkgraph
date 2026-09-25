# Task-context handoff delivery provenance

OpenWorkGraph distinguishes three different facts around agent context:

1. task context was available and fingerprinted;
2. a verified handoff envelope was prepared or passed to the runtime;
3. what the model actually read, used, or followed.

Only the first two can be asserted structurally by an integration. OpenWorkGraph does **not** claim the third.

## Statuses

An integration may explicitly attach one of these statuses to a `run_started` event:

- `prepared` — the integration reports that the integrity-checked `AgentContextHandoff` envelope was prepared for the runtime;
- `delivered_to_runtime` — the integration reports that it successfully passed that verified envelope through its runtime-facing context interface.

If no status is supplied, execution traces expose `not_asserted`. That means only that OWG has no delivery assertion. It does not mean context was not delivered.

## Example

```python
from adapters.context_delivery import attach_context_delivery_to_run_started
from adapters.context_handoff import build_context_handoff
from adapters.task_preflight import TaskPreflightClient

preflight = TaskPreflightClient().try_preflight(
    family_key="agent:example.workflow",
)
handoff = build_context_handoff(preflight)

# Caller-specific step: pass handoff.as_dict() through the runtime's supported
# contextual-data interface. Only after that interface accepts it should an
# integration claim delivered_to_runtime.

run_started = attach_context_delivery_to_run_started(
    {
        "observed_at": "2026-09-26T00:00:00Z",
        "agent_name": "Example Agent",
        "operation": "run_started",
        "status": "running",
        "observation_level": "native_trace",
        "run_id": "runtime-owned-run-id",
    },
    handoff,
    status="delivered_to_runtime",
)
```

## What delivery does not prove

`delivered_to_runtime` is an adapter-reported transport fact. It does not prove that:

- a model read the context;
- a model attended to or remembered it;
- a model used it in reasoning;
- the context caused any later action or outcome;
- the model complied with declared policy;
- the server independently witnessed delivery.

Canonical evidence therefore records `model_context_consumption_attested = false`, and the execution-trace API exposes the same limitation.

## Backward compatibility

Existing adapters do not need to change. A legacy task-context linkage with no delivery field remains valid and is shown as `handoff_status = not_asserted` in the derived execution trace.

No native Claude Code, Codex, OpenAI Agents, or other integration is automatically changed by this feature.

## Privacy

Delivery provenance contains only a tiny structural status plus the task-context linkage already introduced earlier. The full handoff envelope is not copied into canonical agent telemetry.

Prompts, model responses, tool arguments/results, and hidden reasoning remain forbidden by the canonical agent-evidence contract.
