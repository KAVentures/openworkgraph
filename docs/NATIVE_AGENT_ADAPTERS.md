# Native agent adapters

OpenWorkGraph can observe supported agent runtimes through their native lifecycle/telemetry surfaces and project only structural workflow evidence into the same canonical event store used by desktop and browser capture.

The native adapters are **optional**. Existing OpenWorkGraph behavior is unchanged until an agent runtime is explicitly configured to send events.

## Privacy model

Native agent payloads are treated as untrusted, potentially content-bearing input.

OpenWorkGraph stores structural facts such as:

- agent/runtime identity;
- run/conversation ID;
- tool name and coarse tool category;
- success/failure status;
- duration;
- model identifier when safely available;
- approval outcome only when native evidence establishes its provenance;
- trace/run relationships.

The adapters deliberately do **not** persist:

- prompts or assistant messages;
- chain-of-thought/reasoning content;
- tool arguments;
- tool results/output;
- transcript paths;
- working-directory paths;
- account email/account IDs;
- arbitrary OpenTelemetry attributes or log bodies.

Both native integrations reuse the dedicated write-only `.agent_ingest_token`. That token can submit agent evidence but cannot read `/v1/events`, exports, summaries, or `/v1/agent-workflows`.

## Claude Code

Claude Code exposes lifecycle hooks such as `SessionStart`, `PostToolUse`, `PermissionRequest`, `SubagentStart`, and `SessionEnd`. Command hooks receive their native JSON payload on stdin.

OpenWorkGraph registers only lifecycle points that provide useful structural evidence:

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

Content-heavy events such as `UserPromptSubmit`, `PreToolUse`, `MessageDisplay`, and `Stop` are intentionally not registered.

`PermissionRequest` is represented as `human_approval_requested` because the hook fires as Claude Code is about to ask the user for permission and the OpenWorkGraph hook itself never supplies a decision. `PermissionDenied` is different: current Claude Code emits it for an automatic permission denial in auto mode, so OpenWorkGraph records it as a denied execution/error rather than falsely claiming that a human denied the action.

### Generate the settings fragment

From the same OpenWorkGraph installation/interpreter that you normally use:

```bash
python -m adapters.claude_code_hook --print-settings
```

Merge the printed `hooks` object into either:

```text
~/.claude/settings.json
```

for your local user, or the relevant project `.claude/settings.json` if you intentionally want project-scoped configuration.

OpenWorkGraph does **not** edit Claude Code settings automatically.

The generated hook uses separate `command` and `args` fields, so interpreter paths containing spaces do not need shell-quoting tricks.

### Failure behavior

The hook bridge is fail-open by design. Invalid JSON, an unavailable OpenWorkGraph server, authentication failure, timeout, or an unexpected adapter exception all result in exit code `0` with no hook decision. OpenWorkGraph observation therefore cannot block or approve Claude Code tool execution.

Set:

```text
OWG_AGENT_ADAPTER_DEBUG=1
```

only when debugging. Even in debug mode the hook prints a fixed notice rather than exception details or native payload content.

## Codex

Current Codex builds support OpenTelemetry log, trace, and metrics exporters. OpenWorkGraph's recommended integration uses the **trace exporter only** because Codex's diagnostic log stream may contain richer content such as tool arguments/results, whereas its trace-safe events expose the structural information OpenWorkGraph needs.

OpenWorkGraph currently maps these Codex structural events:

```text
codex.conversation_starts -> run_started
codex.tool_result         -> tool_call
codex.api_request         -> model_call
```

The parser also understands `codex.tool_decision` if a compatible relay explicitly sends such a record, but the default configuration below does not enable Codex log export merely to obtain approval events. A tool decision is represented as `human_approval_received` only when Codex explicitly marks its decision source as `user`; decisions resolved by an automated reviewer, or records with no decision provenance, are ignored rather than attributed to a person.

### Generate a safe trace-exporter snippet

Preview a snippet with a placeholder credential:

```bash
python -m adapters.codex_config
```

Or explicitly print a complete snippet containing the local **write-only** agent token:

```bash
python -m adapters.codex_config --with-token
```

Merge those keys into `~/.codex/config.toml`.

If your Codex config already has an `[otel]` section, merge the generated keys into that existing section rather than adding a second `[otel]` table.

The generated configuration is equivalent to:

```toml
[otel]
log_user_prompt = false
log_agent_responses = false
log_guardian_assessments = false
trace_exporter = { otlp-http = { endpoint = "http://127.0.0.1:8787/agent-ingest/v1/codex-otel", headers = { Authorization = "Bearer WRITE_ONLY_TOKEN" }, protocol = "json" } }
```

No log exporter or metrics exporter is added by OpenWorkGraph.

### Codex ingestion behavior

The dedicated endpoint is:

```text
POST /agent-ingest/v1/codex-otel
```

It accepts OTLP/HTTP JSON under the same 2 MB request bound as the generic agent-ingest path and processes at most 1,000 log/span-event records per request.

The adapter uses an allowlist. OTLP `body`, prompt content, tool arguments/output, account identifiers, arbitrary span attributes, and arbitrary span names are ignored. Stable native request/call identifiers may be hashed solely to make repeated log/trace copies idempotent; the original identifiers are not added as content fields.

## Local-first boundary

The Claude bridge defaults to:

```text
http://127.0.0.1:8787
```

and refuses a non-loopback OpenWorkGraph API URL unless `OWG_AGENT_ALLOW_REMOTE=1` is explicitly set. It can only call `/agent-ingest/*` write routes.

The normal OpenWorkGraph launcher binds the local API to `127.0.0.1`, so the native integrations remain local by default.

## What this does not do

These adapters do not:

- scrape terminal output or transcript files;
- alter agent prompts or instructions;
- inject context into an agent;
- proxy tool calls;
- change Claude Code/Codex approval decisions;
- make OpenWorkGraph a dependency for agent execution;
- claim to observe internal model reasoning.

They provide structural execution evidence. Retrieval of learned organizational workflow context back into agents remains a separate layer built on top of the shared OpenWorkGraph evidence store.
