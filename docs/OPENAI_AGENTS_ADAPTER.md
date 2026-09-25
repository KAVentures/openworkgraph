# OpenAI Agents SDK adapter

OpenWorkGraph can observe structural execution from applications built with the OpenAI Agents SDK for Python by registering an **additional tracing processor**.

This integration is optional. OpenWorkGraph itself does not depend on `openai-agents`, and existing desktop/browser/Claude Code/Codex behavior is unchanged when the adapter is unused.

## What it observes

The processor projects a privacy-minimized structural view into the existing OpenWorkGraph agent evidence contract:

- trace start/end -> agent run start/end;
- generation/response/transcription/speech spans -> model calls;
- function spans -> tool calls;
- handoff spans -> handoff events;
- parent/child span relationships;
- safe agent/model/tool identifiers when they pass the adapter's strict structural-label rules;
- duration and allowlisted aggregate token counts when exposed by the native span.

Native trace/span IDs are hashed before persistence. This matters because the SDK permits callers to provide their own IDs.

## What it deliberately does not read or persist

The adapter does not call native trace/span `export()` methods and does not read or persist:

- workflow/trace names;
- trace `group_id`;
- trace metadata;
- prompts or model input;
- model output or assistant messages;
- function/tool input;
- function/tool output;
- MCP payload contents;
- handoff `from_agent` / `to_agent` payload objects;
- model configuration payloads;
- error text;
- chain-of-thought/reasoning content;
- arbitrary native span fields.

The adapter uses only structural allowlisted properties. The generic OpenWorkGraph agent-evidence validator validates the projected event again before it enters the delivery queue and the server validates it again before persistence.

## Failure behavior

Agent observation is fail-open.

OpenAI Agents tracing callbacks are synchronous, so the adapter does not perform the local HTTP request inside those callbacks. Instead it validates the structural event and submits it to a bounded in-memory queue. A daemon worker batches writes to the existing write-only `/agent-ingest/v1/events` endpoint.

If the queue is full, OpenWorkGraph is unavailable, authentication fails, or delivery encounters an unexpected exception, telemetry is dropped rather than blocking or failing the agent application.

Set `OWG_AGENT_ADAPTER_DEBUG=1` only while debugging. Debug output is fixed text and never includes exceptions or native payloads.

## Install the processor in an agent application

The agent application's Python environment must be able to import the OpenWorkGraph `adapters` package. During development this can be the OpenWorkGraph checkout/editable install; a separately packaged lightweight integration can be added later without changing this processor contract.

With OpenWorkGraph running locally:

```python
from adapters.openai_agents import install_openai_agents_processor

owg_processor = install_openai_agents_processor()
```

Then use the Agents SDK normally. The helper calls the SDK's additive trace-processor registration API; it does not replace the SDK's existing tracing processors.

For short-lived processes, keep the returned processor if you want to explicitly flush its local queue before exit:

```python
owg_processor.force_flush()
```

The SDK's normal tracing-provider flush also forwards flush calls to registered processors in current SDK versions.

## Authentication and local-first boundary

The adapter reuses OpenWorkGraph's dedicated write-only `.agent_ingest_token` through the same local agent client used by the other native adapters.

By default it sends only to:

```text
http://127.0.0.1:8787/agent-ingest/v1/events
```

The write-only agent credential cannot read OpenWorkGraph history, summaries, exports, or workflow views. The shared adapter HTTP client refuses non-loopback OpenWorkGraph URLs unless `OWG_AGENT_ALLOW_REMOTE=1` is explicitly set.

If the agent application cannot access the same local OpenWorkGraph auth directory, provide the write-only token through `OWG_AGENT_INGEST_TOKEN` in that application environment.

## Important: this does not change OpenAI's own trace export

OpenWorkGraph is registered as an **additional** processor. It does not disable, replace, filter, or reconfigure any tracing/export behavior the OpenAI Agents SDK application already has.

Therefore:

- the OpenWorkGraph processor stores only the minimized structural projection described above;
- any separate OpenAI/default/custom trace processor continues according to that application's own tracing configuration;
- deciding whether sensitive model/tool data should be included in another processor's traces is a separate application-level privacy decision.

Do not treat OpenWorkGraph's privacy projection as a global filter in front of other tracing processors.

## Deliberate non-goals

This adapter does not:

- modify agent prompts or instructions;
- inject organizational context into a run;
- proxy or approve tool calls;
- observe private model reasoning;
- make OpenWorkGraph availability a prerequisite for agent execution;
- infer success of an entire run from a trace end event when the SDK does not expose a trustworthy run-level outcome.

The next layer—retrieving reusable successful workflow patterns back into agents—should be built separately over the canonical structural evidence rather than coupled to this ingestion adapter.
