# Native agent adapters

OpenWorkGraph can observe supported agent runtimes through their native lifecycle and telemetry surfaces, then project only structural execution evidence into the canonical event store used by desktop and browser capture.

Native adapters are **optional**. Existing OpenWorkGraph behavior is unchanged until an agent runtime is explicitly configured to send events.

## Privacy model

Native agent payloads are treated as untrusted, potentially content-bearing input. OpenWorkGraph stores only structural facts such as runtime identity, run/turn linkage, tool name/category, model identifier, token counters, status, duration, approval provenance when available, and parent/child execution relationships.

The adapters deliberately do **not** persist prompts, assistant messages, reasoning/chain-of-thought, tool arguments, tool results/output, transcript paths, working-directory paths, account identifiers, arbitrary OpenTelemetry attributes, arbitrary span names, or raw log bodies.

### Tool detail (content-free)

Hook-based adapters (Claude Code, Cursor) and Codex's log events see a tool call's input and output in memory. From them, and only in memory, OpenWorkGraph derives a few structural facts per tool call (`shared/tool_detail.py`):

| Field | What it holds | Example |
|---|---|---|
| `commands` | well-known programs that ran, from a fixed allowlist | `["pytest", "git"]` |
| `git` / `gh` | allowlisted git / GitHub CLI operations | `["commit"]`, `["pr_create"]` |
| `tests_passed` / `tests_failed` | numbers from a recognised test-runner summary (pytest, jest, vitest, cargo, node --test, unittest), only when a test command ran | `9` / `2` |
| `file_types` | allowlisted extensions | `["py", "md"]` |
| `file_refs` | keyed hashes of file paths (`f:` + 16 hex). They show "the same file again" without revealing the path. The key (`.agent_file_ref_key`) never leaves this computer, so refs cannot be compared across devices | `["f:3a9c…"]` |
| `lines_added` / `lines_removed` | counts from the edit's patch | `2` / `1` |

Arguments, paths, file names, output text and unknown programs are dropped. Every value passes one allowlist gate (`sanitize_detail`) again when it is stored, so a buggy or hostile sender cannot put text in these fields. Quoted text (a commit message, an `echo` string) never counts as a command. Set `OWG_AGENT_TOOL_DETAIL=0` to turn tool detail off.

Each run in the agent traces gets a `work_summary`: commands, git/gh operations, how many test runs and whether the run **ended** with passing or failing tests, files edited vs only read, file types, lines changed and total tokens. MCP `get_agent_runs` returns it.

Known limits: Codex `apply_patch` paths are relative to the workspace while Claude's are absolute, so the same file can get different refs across the two agents. A shell command that only runs a script (`./deploy.sh`) shows no commands, because script names are not on the allowlist.

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

1. **Lifecycle hooks** for session boundaries, turn boundaries (one prompt = one turn), completed tools, permission requests, failures, and subagent start/stop.
2. **Claude Code OpenTelemetry log events** for per-prompt model calls, token usage, model identity, completed tools, permission decisions, retries and subagent completion.

The hook surface remains useful as a fail-open lifecycle/fallback channel. The OTel log surface supplies the signals hooks do not expose, especially model calls and token usage.

OpenWorkGraph registers these hook points:

```text
SessionStart
SessionEnd
UserPromptSubmit
Stop
PostToolUse
PostToolUseFailure
PermissionRequest
PermissionDenied
SubagentStart
SubagentStop
StopFailure
```

`UserPromptSubmit` and `Stop` carry content: the prompt text and the last assistant message. OpenWorkGraph uses them only as turn boundaries. The adapter reads just `session_id`, `prompt_id` and the event name, and never copies a content field.

| Hook | Becomes |
|---|---|
| `SessionStart` / `SessionEnd` | the session starts / finishes (`run_id` = session) |
| `UserPromptSubmit` / `Stop` | a turn starts / finishes successfully (`run_id` = `prompt_id`, which Claude's tool hooks and OTel share) |

- **No `prompt_id`:** a turn hook without one is ignored. Falling back to the session would make a turn's `Stop` look like the whole session finishing.
- **Not registered:** `PreToolUse` and `MessageDisplay`.
- **`PreCompact`:** not mapped yet. It needs a new operation in the agent event schema, and older Gateways would reject that when syncing.

**Existing installs:** when OpenWorkGraph starts with Claude Code Observe on, it adds any hook events its entries are missing. It keeps a backup, keeps your own hooks and env, and never installs hooks you have not turned on.

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

**Diagnostics:** `GET /v1/agent-telemetry/diagnostics` (and the Connections row in the dashboard) reports per channel since OpenWorkGraph started:

- requests received and when;
- rejections by reason (`auth`, `invalid_payload`, `too_large`, `not_an_object`, `adapter_error`);
- OTel records seen versus ignored;
- events stored.

It also shows which exporters and hook events are configured. So "no model calls" can be traced to one of:

- Claude not exporting;
- a rejected request;
- records the adapter does not recognise.

It holds counts, timestamps and reason codes only, never payload content.

The integration uses Claude's logs exporter only. OWG v0.90 does **not** require Claude's beta detailed-trace exporter.

### Hook failure behavior and delayed delivery

If a hook cannot reach OpenWorkGraph (not running, restarting, busy past the 0.75 s timeout, or a server error), the event may be written to a short local spool and delivered later. This only happens under a recording lease:

- **Who grants it:** a running, recording, non-demo OpenWorkGraph issues the lease. It lasts 90 s and is renewed while recording.
- **When it ends:** it is revoked immediately on Pause, Stop and exit.
- **What the hook checks:** the hook also reads the capture state of the data folder that issued the lease, and spools only if it says "recording".
- **What happens otherwise:** after Stop, or when OpenWorkGraph is not running, agent events are dropped, never collected for later.
- **What doesn't qualify:** a rejection (4xx) is never spooled.
- **On delivery:** spooled events go through the normal ingest path again, so Observe switches, deletions, retention and pause windows apply.
- **Limits:** at most 2,000 files of 256 KB each, and anything older than 24 h is discarded. Files live under `data/auth/agent_spool`, which only your user can read.

Hooks remain asynchronous and fail-open. Invalid JSON, an unavailable OpenWorkGraph server, authentication failure, or an adapter exception cannot block or approve Claude Code execution. `OWG_AGENT_ADAPTER_DEBUG=1` prints only a fixed diagnostic notice, never native exception/payload content.

## Codex

OpenWorkGraph's Codex integration uses **both Codex's structural OTLP log exporter and trace exporter**. The log layer is required for Codex business events such as API requests, completed tools, approval decisions and multi-agent communication; the trace layer supplies native span hierarchy. Content-bearing opt-ins stay disabled, and the server independently strict-allowlists every accepted field.

The adapter maps structural evidence for:

```text
conversation start       -> run_started
response.completed       -> model_call (with token usage)
failed API request       -> model_call (error)
completed tool           -> tool_call
user-sourced decision    -> human_approval_received
multi-agent spawn/send   -> handoff
```

Codex reports token usage on its `codex.sse_event` record for `response.completed` (`input_token_count`, `output_token_count`, `cached_token_count`, `tool_token_count` as the total), not on `codex.api_request`. So each completed response becomes one model call with its tokens; a successful API request adds nothing (it would count the same call twice), and a failed attempt is a model call with status error. Builds that do put usage on the API request are still accepted.

Codex exports each business event twice, as a log record and as a trace span event with the same event ID. Only the log copy carries the tool's arguments and output, from which tool detail is derived. Whichever copy is stored first, a later copy may add missing tool detail or token usage to the stored row. It never overwrites a stored value.

Where available, Codex turn IDs become run boundaries and parent span attributes contribute model identity and token counters. Multi-agent communication is conservative: only a structural `spawn` sent to another agent is treated as a handoff; ordinary inter-agent message/result content is ignored.

Some Codex builds have emitted tracing call-site names in `event.name` rather than the semantic event name. The adapter can infer the supported event class from a small set of low-cardinality structural fields, never from record bodies, arguments, output, message content, or arbitrary span names.

### One-click setup

**Connect → Codex** appends a clearly marked managed `[otel]` block to `~/.codex/config.toml` (or `$CODEX_HOME/config.toml`) after writing a backup. If the file already has its own `[otel]` settings or invalid TOML, OWG changes nothing and requests manual merge. Disconnect removes only the managed block.

The generated configuration explicitly keeps prompt, agent-response and guardian-rationale logging disabled while pointing both the structural log exporter and trace exporter at the same local write-only endpoint:

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

Use the signal-specific endpoint exactly as shown. The generic endpoint accepts OTLP/HTTP JSON and **does not accept protobuf bodies**. It also **does not currently expose the conventional `/v1/traces` alias**; use `/agent-ingest/v1/otel` exactly.

## Local-first boundary

The normal OWG launcher binds the observer API to `127.0.0.1`. Native integrations receive only the write-only agent-ingest credential and remain local by default.

When organization Gateway synchronization is enabled, agent evidence remains local unless the endpoint owner separately opts in with `gateway.local_policy.allow_agent_events: true`; see `docs/AGENT_GATEWAY_SHARING.md`.

## What these adapters do not do

They do not scrape terminal output or transcripts, alter agent prompts/instructions, inject context into an agent, proxy tool calls, change approval decisions, make OWG a dependency for agent execution, or claim access to internal model reasoning.

They provide provider-neutral **structural execution evidence**. Retrieval of learned work context back into agents is a separate layer over the same canonical OpenWorkGraph store.
