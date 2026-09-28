# OpenWorkGraph (unreleased, planned v0.99): capture coverage

Builds on v0.98's capture correctness. It widens what OpenWorkGraph can see without changing the privacy model: structure only, no content, the same Observe switches.

## Observe GitHub Copilot, Gemini CLI and Cursor
The Observe switch now works for three more apps. As with Claude Code and Codex, it backs up the file, keeps your settings, refuses rather than overwrite your own telemetry, and Remove takes out only what OpenWorkGraph added.

- **GitHub Copilot in VS Code:**
  - **How:** Copilot's own OpenTelemetry export, with content capture explicitly off.
  - **What is recorded:** agent runs, model calls (model, tokens) and tool calls from Copilot's GenAI spans. Tool arguments and results are never read.
  - **When it applies:** after VS Code reloads its window.
- **Gemini CLI:**
  - **How:** OTLP over HTTP with `logPrompts` forced **false**. Gemini's default is true, which puts prompts and tool arguments in its log events.
  - **What is recorded:** turn starts, model calls, tool calls, and human accept/reject decisions (auto-accept is not a person).
  - **Allowlist:** nothing else is read even if prompt logging is re-enabled.
- **Cursor:**
  - **How:** hooks for sessions, turns (`beforeSubmitPrompt` → `stop`, one per generation), tool results and subagent outcomes.
  - **Never blocks:** Cursor runs hooks synchronously, so the hook answers first with a non-blocking reply. It never uses a permission hook, so it cannot approve or deny anything.
- **Authentication:**
  - Copilot and Gemini cannot send an Authorization header from a settings file, so their OTLP endpoint path carries a separate write-only token.
  - The access log shows it as `[redacted]`.
  - Only OTLP/HTTP JSON is accepted; protobuf gets a clear 415.
- **Diagnostics:** each new app has its own line (Copilot OTel, Gemini OTel, Cursor hooks) in Connections.
- **Agents tab:** the "past runs hidden" note now names only apps whose runs were actually hidden.

## Clipboard writes
- **What is new:** copies made without a shortcut (menu, right-click, drag, an app) are now noticed from the OS clipboard change counter, as `clipboard_write`. The counter never exposes contents.
- **No double counting:** a Cmd/Ctrl+C that already produced `clipboard_copy` is not counted twice.
- **Paste linking:** a later paste links to whichever write came last.
- **What it never does:** a write is never turned into a paste, since pastes do not change the counter; pastes are still shortcut-only. Nothing is attributed while you are away.
- **Opting out:** `clipboard_write_detection_enabled: false`.
- **Timing fix:** click, scroll and clipboard events are now stamped with the time they happened, not when the worker thread processed them.

## Web agents in any UI language
The browser sensor recognises ChatGPT, Claude, Gemini, Microsoft Copilot and Lovable runs from structure first:
- `aria-busy` and streaming markers;
- stable `data-testid` tokens for send and stop;
- submit buttons in the composer's form;
- Enter in the message box (not Shift+Enter or IME composition).

The English button labels are only a fallback, so a Swedish or German UI works whenever the site exposes any of these. The browser sensor version is 1.13.0; reload the extension to update.

## Not in this release
- Claude Code metrics export.
- Copilot CLI (environment-variable configuration only).
- Platform rework (macOS without `osascript`, Windows UWP app names, Linux/Wayland).
