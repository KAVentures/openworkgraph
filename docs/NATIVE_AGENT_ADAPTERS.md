# Native agent adapters

OpenWorkGraph can observe supported agent runtimes through their native lifecycle and telemetry surfaces, then project only structural execution evidence into the canonical event store used by desktop and browser capture.

Native adapters are **optional**. Existing OpenWorkGraph behavior is unchanged until an agent runtime is explicitly configured to send events.

## Privacy model

Native agent payloads are treated as untrusted, potentially content-bearing input. OpenWorkGraph stores only structural facts such as runtime identity, run/turn linkage, tool name/category, model identifier, token counters, status, duration, approval provenance when available, and parent/child execution relationships.

The adapters deliberately do **not** persist prompts, assistant messages, reasoning/chain-of-thought, tool arguments, tool results/output, transcript paths, working-directory paths, account identifiers, arbitrary OpenTelemetry attributes, arbitrary span names, or raw log bodies.

Native integrations reuse the dedicated write-only `.agent_ingest_token`. That token can submit evidence but cannot read work history, exports, summaries, agent reports, or MCP context.

## Capability semantics

Agent reports separate **observed count** from **adapter capability**:

- `observable`: the active adapter is designed to expose the signal; `0` means zero matching events were observed in this evidence window.
- `partial`: the signal is exposed only on some runtime/span paths.
- `not_observable`: the integration does not expose that signal; the dashboard renders `—`, never a misleading zero.
- `unknown`: the integration has not declared a portable capability for that signal.

Missing evidence is never treated as proof that an underlying agent action did not occur, and hidden model reasoning is never claimed as observable.

## Claude Code

### Rich observation: hooks + stable OpenTelemetry logs

OpenWorkGraph combines two Claude Code surfaces:

1. **Lifecycle hooks** for session boundaries, completed tools, permission requests, failures, and subagent start/stop.
2. **Claude Code OpenTelemetry log events** for per-prompt model calls, token usage, model identity, completed tools, permission decisions, retries and subagent completion.

The hook surface remains useful as a fail-open lifecycle/fallback channel. The OTel log surface supplies the signals hooks do not expose, especially model calls and token usage.

OpenWorkGraph registers only content-safe hook points:

```text
SessionStart
SessionEnd
PostToolUse
PostToolUseFailure
PermissionRequest
PermissionDenied
SubagentStart
SubagentStop
StopFailure
```

Content-heavy hooks such as `UserPromptSubmit`, `PreToolUse`, `MessageDisplay`, and `Stop` are intentionally not registered.

The Claude OTel adapter recognizes these stable structural events:

```text
claude_code.user_prompt
claude_code.api_request
claude_code.api_error
claude_code.api_refusal
claude_code.tool_result
claude_code.tool_decision
claude_code.api_retries_exhausted
claude_code.subagent_completed
```

`prompt.id` from OTel and `prompt_id` from current Claude Code hooks are used as a structural turn correlation key. This lets a single user request become one observed execution containing its model/tool/handoff sequence instead of collapsing an entire terminal session into one run. Older hook payloads without `prompt_id` safely fall back to the Claude session boundary.

`SubagentStart` produces a parent `handoff` event plus a child run boundary. `subagent_completed` enriches the parent execution from native telemetry. When hooks and OTel report the same completed tool, the read-side report prefers the richer OTel event so the tool is not double-counted.

`PermissionRequest` is `human_approval_requested`. A Claude OTel `tool_decision` becomes `human_approval_received` only when its decision source is explicitly human (`user_temporary`, `user_permanent`, `user_reject`, or `user_abort`). Automated config/hook decisions are not attributed to a person.

### One-click setup

On **Connect → Claude Code**, click **Connect**. OpenWorkGraph adds:

- its safe asynchronous hooks; and
- logs-only OTel settings pointing at the local write-only endpoint.

It writes a timestamped backup first, preserves every unrelated setting/hook, replaces rather than duplicates older OWG hooks, and refuses to overwrite an existing conflicting telemetry destination. Disconnect removes only OWG hooks and telemetry values that still exactly match what OWG wrote; if the user changed a value afterwards, OWG leaves it alone.

The managed Claude settings explicitly keep content logging disabled:

```text
OTEL_LOG_USER_PROMPTS=0
OTEL_LOG_ASSISTANT_RESPONSES=0
OTEL_LOG_TOOL_DETAILS=0
OTEL_LOG_TOOL_CONTENT=0
OTEL_LOG_RAW_API_BODIES=0
```

This is defense in depth. The server-side Claude adapter independently uses a strict allowlist and never copies content-bearing attributes even if a user changes upstream logging settings later.

The dedicated local endpoint is:

```text
POST /agent-ingest/v1/claude-otel
```

The integration uses Claude's logs exporter only. OWG v0.90 does **not** require Claude's beta detailed-trace exporter.

### Hook failure behavior

Hooks remain asynchronous and fail-open. Invalid JSON, an unavailable OpenWorkGraph server, authentication failure, or an adapter exception cannot block or approve Claude Code execution. `OWG_AGENT_ADAPTER_DEBUG=1` prints only a fixed diagnostic notice, never native exception/payload content.

## Codex

OpenWorkGraph's default Codex integration uses the native OTLP trace exporter and does not enable content-rich diagnostic log export.

The adapter maps structural evidence for:

```text
conversation start       -> run_started
API request              -> model_call
completed tool           -> tool_call
user-sourced decision    -> human_approval_received
multi-agent spawn/send   -> handoff
```

Where available, Codex turn IDs become run boundaries and parent span attributes contribute model identity and token counters. Multi-agent communication is conservative: only a structural `spawn` sent to another agent is treated as a handoff; ordinary inter-agent message/result content is ignored.

Some Codex builds have emitted tracing call-site names in `event.name` rather than the semantic event name. The adapter can infer the supported event class from a small set of low-cardinality structural fields, never from record bodies, arguments, output, message content, or arbitrary span names.

### One-click setup

**Connect → Codex** appends a clearly marked managed `[otel]` block to `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) after writing a backup. If the file already has its own `[otel]` settings or invalid TOML, OWG changes nothing and requests manual merge. Disconnect removes only the managed block.

The generated configuration disables prompt/agent-response/guardian logging and points only the trace exporter at:

```text
POST /agent-ingest/v1/codex-otel
```

## OpenAI Agents SDK

For Python applications using the OpenAI Agents SDK, OWG registers an **additional tracing processor** rather than replacing the application's existing tracing.

The processor observes structural trace/run boundaries plus:

- generation/response/transcription/speech spans as `model_call`;
- function/MCP spans as `tool_call`;
- native handoff spans as `handoff`;
- parent-child span linkage;
- duration and error state;
- model identity and token usage where the SDK span exposes them.

Response span usage/model fields are read directly without serializing response content. Approval request/decision events are currently marked `not_observable` for this adapter rather than shown as zero.

Install in the agent application:

```python
from adapters.openai_agents import install_openai_agents_processor

install_openai_agents_processor()
```

## Generic OpenTelemetry

For runtimes that expose ordinary GenAI OTLP traces rather than a dedicated OWG adapter:

```text
POST /agent-ingest/v1/otel
```

OWG accepts OTLP/HTTP JSON and projects a strict portable subset of GenAI semantic conventions. Portable model calls, tool calls, usage, model identity and timings can be observed when emitted. Provider-specific handoffs and human approvals are **not** guessed; those capabilities remain unavailable unless a native adapter provides them.

Example:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://127.0.0.1:8787/agent-ingest/v1/otel"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="http/json"
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer WRITE_ONLY_AGENT_TOKEN"
```

Use the signal-specific endpoint exactly as shown. The generic endpoint currently accepts JSON, not OTLP protobuf or gRPC.

## Local-first boundary

The normal OWG launcher binds the observer API to `127.0.0.1`. Native integrations receive only the write-only agent-ingest credential and remain local by default.

When organization Gateway synchronization is enabled, agent evidence remains local unless the endpoint owner separately opts in with `gateway.local_policy.allow_agent_events: true`; see `docs/AGENT_GATEWAY_SHARING.md`.

## What these adapters do not do

They do not scrape terminal output or transcripts, alter agent prompts/instructions, inject context into an agent, proxy tool calls, change approval decisions, make OWG a dependency for agent execution, or claim access to internal model reasoning.

They provide provider-neutral **structural execution evidence**. Retrieval of learned work context back into agents is a separate layer over the same canonical OpenWorkGraph store.
