# Connecting AI apps

OpenWorkGraph has one list of AI apps, on the dashboard's **Connect** tab under **Connections**. Each app has up to two switches:

| Switch | What it does |
|---|---|
| **Context** | The app can read the work context you allow, through OpenWorkGraph's local MCP server. |
| **Observe** | OpenWorkGraph records how the agent runs: runs, tools, failures, approvals, timings. Prompts, responses, reasoning, tool arguments and tool results are never collected. |

| App | Context | Observe | Config file OpenWorkGraph edits |
|---|---|---|---|
| Claude Code | ✓ | ✓ hooks | `~/.claude.json` (MCP), `~/.claude/settings.json` (hooks) |
| Claude Desktop | ✓ | – | `claude_desktop_config.json` in Claude's app-support folder |
| Codex | ✓ | ✓ OTel traces | `~/.codex/config.toml` (or `$CODEX_HOME`) |
| Cursor | ✓ | ✓ hooks | `~/.cursor/mcp.json` (MCP), `~/.cursor/hooks.json` (hooks) |
| VS Code + GitHub Copilot | ✓ | ✓ OTel traces | `mcp.json` (MCP) and `settings.json` (telemetry) in VS Code's user folder |
| Windsurf | ✓ | – | `~/.codeium/windsurf/mcp_config.json` |
| Gemini CLI | ✓ | ✓ OTel logs | `~/.gemini/settings.json` (`mcpServers` and `telemetry`) |
| GitHub Copilot CLI | ✓ | – | `~/.copilot/mcp-config.json` (or `$COPILOT_HOME`) |
| Kiro | ✓ | – | `~/.kiro/settings/mcp.json` |
| Amazon Q Developer | ✓ | – | `~/.aws/amazonq/mcp.json` |

**Cloud apps** (ChatGPT, Lovable, Microsoft 365 Copilot) run on their own servers and accept only remote HTTP MCP servers, so they cannot reach OpenWorkGraph on this computer directly. They need a secure tunnel to the optional local HTTP endpoint (or an organization Gateway). Other MCP apps and custom agents (OpenAI Agents SDK, OpenTelemetry, custom events) are under **More ways to connect**.

## How Copilot, Gemini CLI and Cursor are observed

**GitHub Copilot (VS Code)**
- **Settings written:** Copilot's own OpenTelemetry export, pointed at OpenWorkGraph (`github.copilot.chat.otel.*`), with `captureContent` false.
- **What becomes evidence:** Copilot's agent, model-call and tool spans. Tool arguments and results are never read.
- **When it applies:** after VS Code reloads its window.
- **Refuses (changes nothing) if:**
  - Copilot telemetry already goes to your own collector;
  - content capture is on;
  - `settings.json` contains comments (VS Code allows them, OpenWorkGraph does not rewrite them). Manual setup shows the four keys to add.

**Gemini CLI**
- **Settings written:** a `telemetry` block in `~/.gemini/settings.json`: `target` local, `otlpProtocol` http, and `logPrompts` **false**. Gemini's default is true, which would put prompts and tool arguments into its log events.
- **What becomes evidence:** turn starts, model calls (model, duration, token counts), tool calls, and human accept/reject decisions. Nothing else is read, even if prompt logging is later turned back on.
- **Refuses (changes nothing) if** you already have your own telemetry settings.

**Cursor**
- **Hooks added:** OpenWorkGraph's hooks in `~/.cursor/hooks.json`, next to yours:
  - `sessionStart` / `sessionEnd`
  - `beforeSubmitPrompt` / `stop` (one turn per generation)
  - `postToolUse` / `postToolUseFailure`
  - `subagentStop`
- **Never blocks:** Cursor runs hooks synchronously, so the hook answers first with a reply that never blocks (`{"continue": true}` for `beforeSubmitPrompt`, `{}` otherwise), then records. Permission hooks (`preToolUse`, `beforeShellExecution`, `beforeMCPExecution`, `beforeReadFile`, `subagentStart`) are never used, so OpenWorkGraph can never approve or deny anything.
- **Never read:** prompts, agent text, tool input/output, file edits, commands, error messages, your email and workspace paths.

**Authentication without headers**
- Copilot and Gemini CLI cannot send an Authorization header from a settings file, so their endpoint is `/agent-ingest/otlp/<source>/<token>/v1/…`. The path token is separate and write-only: it can only add structural agent events and cannot read anything.
- The server's access log shows the path as `[redacted]`.
- Only OTLP/HTTP JSON is accepted. Protobuf gets a clear 415, and the diagnostics line reports it.

## How the switches behave

- **First time on.** OpenWorkGraph adds its entry to the app's config file. The app picks it up when it next starts or reloads; the dashboard says which.
  - It writes a timestamped `*.owg-backup-*` copy first.
  - It keeps every other setting and server.
  - It never duplicates its own entry.
  - It refuses (and changes nothing) if the file is not valid JSON/TOML, or if you already have a hand-written entry it should not overwrite.
- **Off / on again.** This only flips a switch that OpenWorkGraph checks on every MCP tool call and every incoming agent event. It takes effect **immediately**, with no restart, and the config stays in place.
- **Restart needed.** Some apps load this config only when they start. The ChatGPT app (Codex engine) does this for Observe, and Claude Desktop does it for Context. If the app has been running since before OpenWorkGraph changed its config, the row says so ("Quit and reopen the ChatGPT app (⌘Q)…"). Opening a new chat is not enough.
- **Agents tab history.** Runs from an agent whose Observe is off or removed are hidden by default. Tick *Show past runs from agents whose Observe is off* to see them. Nothing is deleted.
- **Remove** deletes OpenWorkGraph's entry from the app's config file (with a backup).
- **Context also needs AI access.** The dashboard's **AI access** master switch (top of **AI apps**, and in **Privacy**) is the one gate for every app. It is off on a new install; since v0.118 your choice is remembered after a restart unless you turn on **Turn AI access off every time OpenWorkGraph starts** (Privacy, Advanced view). Turning a Context switch on also turns AI access on.

## For agents and scripts

The same switches are available as a command that works from any directory and prints JSON. The dashboard shows the exact command for your install under **For agents and scripts**. On macOS it is typically:

```bash
owg=("$HOME/Library/Application Support/WorkflowObserver/.venv/bin/python" "$HOME/Library/Application Support/WorkflowObserver/owg_connect.py")
"${owg[@]}" list                     # every app, what is installed, what is on
"${owg[@]}" on claude_code           # context + observe
"${owg[@]}" on cursor --mcp          # only context
"${owg[@]}" off claude_code --observe  # instant, no restart
"${owg[@]}" remove codex             # uninstall from the app's config
```

Exit code `0` means success. Exit code `2` with `"manual_setup_required": true` means the file could not be changed safely, and nothing was changed.

The dashboard uses `GET /v1/connections` and `POST /v1/connections` with `{"client": "cursor", "kind": "mcp" | "observe" | "both", "action": "on" | "off" | "remove"}`. Both routes are under the authenticated `/v1` boundary.

Switching connections is deliberately **not** exposed as an MCP tool. An app connected for context should not be able to widen its own or another app's access.
