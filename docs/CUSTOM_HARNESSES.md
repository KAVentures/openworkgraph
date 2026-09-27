# Custom agent harnesses

OpenWorkGraph can work with an arbitrary agent runtime, including self-built harnesses and frameworks that OpenWorkGraph does not know by name.

There are two independent connections:

```text
OpenWorkGraph -- MCP context --> agent / harness
OpenWorkGraph <-- structural telemetry -- agent / harness
```

You can use either direction or both. Sending telemetry never grants read access to work history. Giving a harness MCP context never automatically enables observation of its execution.

## 1. Agent -> OpenWorkGraph: structural execution

OpenWorkGraph accepts the same vendor-neutral structural contract used by its native integrations. Useful operations include:

- `run_started` / `run_finished`
- `model_call`
- `tool_call`
- `handoff`
- `human_approval_requested` / `human_approval_received`
- `error`

The contract can also preserve `run_id`, `trace_id`, `span_id`, `parent_span_id`, `workflow_id`, timing, a coarse tool category, model identifier and token counts when the runtime exposes them.

The content boundary does not change for custom harnesses. Do not send prompts, model responses, messages, chain-of-thought/reasoning, tool arguments, tool results, returned values or exception text. The server rejects content-bearing fields in direct structural batches.

### Python

The repository ships a standalone, dependency-free Python helper in `sdk/python/openworkgraph_agent.py`. It can run in the harness's own environment and is also independently installable from the repository subdirectory.

```python
from openworkgraph_agent import AgentObserver

owg = AgentObserver("my-agent", framework="my-harness")
try:
    with owg.run(workflow_id="optional-workflow-id") as run:
        with run.model(model="model-id"):
            call_model()
        with run.tool("repository_search", category="search"):
            search_repository()
finally:
    owg.shutdown()
```

The SDK uses a bounded background queue and is fail-open: an unavailable OpenWorkGraph observer drops telemetry rather than blocking or failing the agent. Returned values and exception messages are never serialized.

### TypeScript / Node

`sdk/typescript/index.mjs` is dependency-free ESM for Node 18+ and `index.d.ts` provides TypeScript declarations.

```js
import {AgentObserver} from "./openworkgraph-agent.mjs";

const owg = new AgentObserver("my-agent", {framework: "my-harness"});
try {
  await owg.withRun(async run => {
    await run.model({model: "model-id"}, async () => callModel());
    await run.tool("repository_search", {category: "search"}, async () => searchRepository());
  });
} finally {
  await owg.shutdown();
}
```

### OpenTelemetry

If the runtime already emits portable GenAI OpenTelemetry spans, point OTLP/HTTP JSON traces at:

```text
http://127.0.0.1:8787/agent-ingest/v1/otel
```

Use the signal-specific traces endpoint, `http/json`, and the installation's dedicated write-only agent-ingest bearer. Unknown spans are ignored rather than guessed.

### Raw HTTP

Any language can post canonical structural events to:

```text
POST http://127.0.0.1:8787/agent-ingest/v1/events
Authorization: Bearer <write-only agent-ingest token>
```

The write-only token cannot read work history, exports, summaries or MCP context.

## 2. OpenWorkGraph -> agent: context over MCP

Any MCP-capable harness can launch the compact local MCP server using the configuration generated in the dashboard under **Connect -> Your own agent / harness**.

The custom harness is still subject to OpenWorkGraph's normal disclosure controls:

- the run-level AI access switch must be ON;
- Redacted remains the default disclosure level unless the user explicitly allows Full and organization policy permits it;
- historical reads require the user's saved-history lease and are restricted to its authorized date range.

The MCP credential/path is separate from the write-only telemetry bearer.

## Observation level

Use the observation level that describes what the harness truly exposes:

- `native_trace` — the runtime itself emits a complete structural trace;
- `instrumented_tools` — hooks/wrappers observe structural tool execution;
- `mcp_only` — only operations crossing an MCP boundary are visible;
- `os_observed` — only desktop/browser structural evidence is available;
- `outcome_only` — only externally visible results are known.

Missing signals mean **not observed**, not that the agent did not perform them.

## OpenClaw, Hermes and other frameworks

A named adapter is not required. If a framework exposes callbacks/hooks, wrap those callbacks with the Python/TypeScript helper or translate them to raw structural events. If it already emits OpenTelemetry, use the OTLP route. If neither is available, OpenWorkGraph can still represent surface-observed activity at a lower observation level.

Do not add provider-specific event vocabulary unless the information is genuinely portable. Thin adapters should translate native events into the shared OpenWorkGraph contract.

## Containers and remote workers

The default local observer binds to loopback. A harness running in a separate container, VM or remote machine may not be able to reach `127.0.0.1:8787` on the host. Do not expose the local observer broadly just to make telemetry convenient. Use an explicitly secured deployment/network path appropriate to the environment; until such a path exists, treat direct SDK ingestion as same-host/local integration.
