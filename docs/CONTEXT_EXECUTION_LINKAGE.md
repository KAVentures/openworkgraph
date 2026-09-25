# Context → execution linkage

OpenWorkGraph can associate a task-context preflight result with the structural agent run that follows it without storing the context body again and without capturing prompt, model output, tool arguments/results, or chain-of-thought.

This is an instrumentation layer, not an enforcement or causal-inference layer.

## Why it exists

The unified task-context API can tell an agent what declared policy and observed procedural evidence are relevant before a task. The preflight client gives that response a deterministic `context_sha256` and exposes the active `policy_manifest_sha256` when present.

A later run can now record that structural identity on its `run_started` event. This makes questions such as these possible later:

- did this observed run have an OWG preflight attempt?
- was the observer available?
- did the task family resolve?
- which exact context snapshot hash was supplied?
- which policy-manifest hash was represented in that snapshot?
- what explicit structural outcome was later observed for the run?

It does **not** answer whether the context caused the outcome.

## Adapter helper

`adapters.context_link` provides:

- `task_context_link(preflight)` — project a `TaskPreflight` into the strict structural linkage object;
- `attach_preflight_to_run_started(event, preflight)` — copy a `run_started` event and attach that linkage.

Example:

```python
from adapters.context_link import attach_preflight_to_run_started
from adapters.task_preflight import TaskPreflightClient

preflight = TaskPreflightClient().try_preflight(
    family_key="agent:workflow:0123456789abcdef",
)

run_started = attach_preflight_to_run_started(
    {
        "observed_at": "2026-09-25T13:00:00+00:00",
        "agent_name": "my-agent",
        "operation": "run_started",
        "status": "running",
        "observation_level": "native_trace",
        "run_id": "runtime-owned-id",
    },
    preflight,
)
```

The normal agent evidence sink/ingest path can then persist this event.

## Persisted linkage fields

The optional `task_context` object is accepted only on `run_started` events and is fail-closed. Its input allowlist is:

- `preflight_attempted=true`;
- `available` boolean;
- `resolved` boolean;
- `context_sha256` — lowercase 64-character SHA-256 when context was available;
- `policy_manifest_sha256` — optional lowercase SHA-256;
- `family_key` — optional validated structural family, required when resolved.

The canonical stored metadata adds:

- `linkage_assertion_source=agent_adapter`;
- `context_snapshot_verified_by_server=false`.

Unknown fields are rejected. The normal recursive content-field blocklist still applies.

An unavailable preflight may record only the attempted/available/resolved booleans. It cannot simultaneously claim a context hash, policy hash, family, or resolution.

## Why the server does not call this an attestation

The hashes are produced by the preflight client from the response it received and then reported by the agent adapter on `run_started`. The ingest server validates their shape and consistency but does not independently reproduce or cryptographically attest the historical context response.

Accordingly, the linkage API always reports:

- `linkage_is_adapter_reported=true`;
- `context_snapshot_server_attested=false`;
- `causal_interpretation=false`.

A future attestation mechanism could be added separately if an enterprise use case requires stronger proof.

## Read-only linkage view

Authenticated local API:

```text
GET /v1/task-context/executions
```

Optional parameters:

- `family_key` — structural family filter;
- `since` — canonical-evidence time lower bound;
- `evidence_limit` — bounded evidence scan;
- `limit` — bounded returned executions;
- `include_unlinked` — include agent executions where no preflight linkage was observed.

The write-only agent-ingestion credential cannot read this endpoint.

Each result exposes only privacy-safe structural information such as:

- hashed `execution_id`;
- observed/preflight family keys;
- outcome status and outcome basis;
- observation level;
- structural-step count;
- linkage status;
- context/policy SHA-256 values;
- hashed linkage-event reference.

Raw run/session/trace/span IDs are not returned.

## Conservative conflict handling

If multiple `run_started` events for the same structural run carry different context linkage objects, OWG reports `conflicting_assertions` and does not select either context/policy hash.

Other linkage states are:

- `context_resolved`;
- `context_available_unresolved`;
- `preflight_unavailable`;
- `not_observed`.

## What this PR does not do

It does not:

- automatically call preflight for every agent runtime;
- inject context into a model prompt;
- recommend or select an action;
- enforce policy;
- persist a new linkage table;
- change the evidence database schema;
- treat absence of a preflight marker as proof that no context existed;
- claim that context improved or harmed an outcome.

Those last comparisons belong to a later effectiveness-analysis layer, where the observational nature of the data must remain explicit.
