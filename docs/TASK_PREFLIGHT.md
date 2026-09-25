# Task-context preflight client

OpenWorkGraph exposes `GET /v1/task-context` and MCP `get_task_context` as the unified read surface for declared organizational policy plus observed procedural evidence.

`adapters.task_preflight.TaskPreflightClient` is a small reusable Python client for agent runtimes that want to inspect that context immediately before a task without implementing the local HTTP/auth details themselves.

## What preflight means

A preflight is a **read** performed before an agent action. It can tell a runtime:

- whether OpenWorkGraph is reachable;
- whether the task family resolved;
- whether declared policy exists for that family;
- how many similar prior runs were returned;
- whether repeated explicit failure patterns or approval hotspots were observed;
- whether the structural policy/observation comparison contains potential divergences.

It does **not** tell the runtime what action to take.

The client deliberately exposes no `allow`, `deny`, `recommended_action`, generated instruction, or prompt formatter. `automatic_enforcement` and `automatic_execution` remain false.

## Example

```python
from adapters.task_preflight import TaskPreflightClient

client = TaskPreflightClient()
preflight = client.try_preflight(
    family_key="human:github.create_issue",
)

if preflight.available and preflight.context_resolved:
    context = preflight.context
    # The consuming runtime decides how to use the structured context.
```

For structural agent workflows:

```python
preflight = client.try_preflight(
    current_steps=[
        "model_call",
        "tool:search:tool:0123456789ab",
    ]
)
```

The underlying task-context resolver still requires OpenWorkGraph-generated structural tokens and only auto-resolves when the prefix uniquely identifies one observed family.

## Failure semantics

There are two intentionally different failure classes.

### Observer unavailable

`try_preflight()` catches only observer/service availability failures and returns:

```text
available = false
resolution_status = unavailable
error_code = observer_unavailable
context = null
```

No raw exception text, URL, token, or runtime content is copied into this object.

This means adding OpenWorkGraph context does not make an otherwise functioning agent runtime crash merely because the local observer is stopped.

### Invalid integration or query

Malformed structural input, unauthorized credentials, rejected queries, invalid response JSON, or a response that violates the authority/read-only contract raises `TaskPreflightError`.

These conditions are **not** silently converted to `available=false`, because doing so could make a broken integration look like benign observer downtime.

## Authority preservation

The client verifies the task-context contract before exposing a successful `TaskPreflight`:

- task context must be marked read-only;
- `writes_performed` must be false;
- automatic execution must be false;
- automatic policy enforcement must be false;
- observed procedure must remain non-authoritative and non-prescriptive.

Declared policy can be authoritative **as declared input**. Repeated observed behavior does not become policy.

The client does not itself prove that a declared policy was cryptographically distributed by the enterprise signing path. That trust decision remains the endpoint-managed policy layer.

## Credentials

Local preflight uses the ordinary OpenWorkGraph local API read credential. It intentionally does not use the write-only agent-ingestion token.

For the default loopback observer, the client can read the local API credential through OpenWorkGraph's existing local-auth helper.

Remote preflight is disabled by default. To use a non-loopback API endpoint, both must be set explicitly:

```text
OWG_PREFLIGHT_ALLOW_REMOTE=1
OWG_API_TOKEN=<read credential>
```

This is separate from `OWG_AGENT_INGEST_TOKEN`.

## Custom transports

Runtimes that already own an authenticated transport can provide a `fetcher`:

```python
client = TaskPreflightClient(fetcher=my_fetcher)
```

The fetcher receives the bounded query-parameter dictionary and returns the decoded `/v1/task-context` JSON object. The same authority validation and summary projection then run locally.

This is intended to make the preflight contract reusable in future Claude, Codex, OpenAI Agents SDK, custom enterprise-agent, or other integrations without coupling those runtimes to a second policy/memory implementation.

## Non-goals

This first preflight slice does not:

- automatically inject context into a model prompt;
- change a system prompt;
- intercept tool calls;
- approve or deny actions;
- require a particular agent framework;
- write execution evidence;
- persist which context was supplied to a run.

Linking a supplied context snapshot/revision to the subsequent agent execution is a separate follow-up layer so retrieval and outcome attribution remain independently auditable.
