# OpenWorkGraph v0.107.0: agent learning loop and trustworthy setup diagnostics

v0.107.0 is the single public release for the v0.101–v0.107 development stack. Intermediate stack slices remain unreleased.

## Setup checks
Per-channel delivery counts already showed *what* arrived. Each Connect card now also gives a specific next step when observed configuration/delivery evidence shows a problem. `/v1/agent-telemetry/diagnostics` returns `checks`:
- **Claude Code hooks arriving but no model/token telemetry:** this is reported as an observation, not as proof of one cause. A common reason is that open sessions started before telemetry was enabled. **Start a new Claude Code session; if telemetry is still absent, switch Observe off and on and re-check.**
- **Claude Code hooks out of date** (missing turn events). **Restart OpenWorkGraph, then start a new session.**
- **Claude Code telemetry not set up.** **Switch Observe off and on.**
- **Token rejected** on any channel. **Switch Observe off and on** to write the current token.
- **OTLP protobuf** received; only OTLP/HTTP JSON is accepted.
- **No Codex telemetry yet** (informational). Codex reads its settings when a session starts.
- **Agent events waiting** while recording is paused or stopped (informational). They expire after 24 hours.
- **Outcome tracking** on without `gh`, or with `gh` logged out.
- **Session briefs** on, but their Claude Code hook is missing from the settings file.

The recency calculation treats an event received exactly now (`age == 0`) as recent rather than accidentally falling through a truthiness check.

## Full v0.107 stack
- **Run memory:** content-free structural run summaries can survive raw-history retention, while upgrades preserve an existing user's explicit both-streams-ephemeral choice by leaving run memory off until they opt in.
- **Outcome tracking:** opt-in `github.com` PR/CI outcome polling through the local `gh` CLI; arbitrary tool-provided hosts are not network targets, and watch timing follows the observed work event.
- **Session briefs:** opt-in bounded Claude Code context from prior structural runs, with unknown test results kept honestly unknown.
- **Human ↔ agent join:** coarse human activity around runs and immediate previous-turn rework attribution, with cross-platform interprocess locking for concurrent async hooks.
- **Brief evaluation:** isolated randomized trials, sticky assignment per trial/session, historical ordinary briefs excluded, and open PRs right-censored from merge-rate denominators.
- **Playbooks:** portable structural workflow summaries whose imported steps must match canonical OpenWorkGraph grammar; arbitrary prompt-like strings and device-local refs are rejected.
- **Release convergence:** root package, Python SDK, TypeScript SDK, MCPB manifest and version consistency test are all `0.107.0`.

## Merge/release procedure
Merge #106 → #107 → #108 → #109 → #110 → #111 → #112 in order. After each parent lands, retarget the next PR to `main` and require fresh green CI. Only #112 carries the `0.107.0` version bump and should trigger the next public tester release.
