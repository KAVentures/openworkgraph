# OpenWorkGraph (unreleased, planned v0.107): is it working?

Builds on v0.106. The per-channel delivery counts showed *what* arrived. Each app's Connect card now also says what to do when something is off, one step per line.

## Setup checks
They come from delivery counts and configuration only; OpenWorkGraph does not guess about apps it cannot see. `/v1/agent-telemetry/diagnostics` now returns `checks`:
- **Claude Code sessions predate telemetry:** hooks are arriving but no telemetry, because telemetry applies to new sessions only. **Start a new Claude Code session.** This was the most common reason for missing model and token data.
- **Claude Code hooks out of date** (missing turn events). **Restart OpenWorkGraph, then start a new session.**
- **Claude Code telemetry not set up.** **Switch Observe off and on.**
- **Token rejected** on any channel (often after a reinstall or a moved data folder). **Switch Observe off and on.**
- **OTLP protobuf** received; only OTLP/HTTP JSON is accepted.
- **No Codex telemetry yet** (informational). Codex reads its settings when a session starts.
- **Agent events waiting** while recording is paused or stopped (informational). They expire after 24 hours.
- **Outcome tracking** on without `gh`, or with `gh` logged out.
- **Session briefs** on, but their Claude Code hook is missing from the settings file.
