# OpenWorkGraph v0.107 stack slice: session-start briefs

This unreleased slice closes the learning loop: an opted-in Claude Code session can start with a short, content-free description of how prior structural work went.

## Brief agents at session start (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → Claude Code. This adds one synchronous `SessionStart` hook, separate from the observation hooks. Turning it off removes only that hook.
- **When it runs:** new, cleared and compacted sessions (not resumed ones).
- **Scope:** prior runs in the same keyed project when available; if the current project has no history yet, the brief clearly labels a cross-project fallback over the past 30 days.
- **What the brief holds:** test outcomes, pull-request outcomes when available, allowlisted commands, typical files/lines and median tokens.
- **Honest unknowns:** if the latest test tool use has no recognized test summary, the brief says the result is unknown and does not invent `0 passed, 0 failed`.
- **Where the facts come from:** raw history and run memory. It works with "Don't keep after session" when run memory is enabled.
- **What it never holds:** titles, paths, prompts or tool content. It is at most 900 characters and labeled observational, not instructions.
- **Preview:** "Preview brief" shows exactly what an agent would receive. The card counts every brief actually sent.
- **Token:** the hook uses its own brief-only token. The write-only ingest token still cannot read anything.
- **Failure:** the hook fails open. After 2 seconds or on any error it adds nothing.

## Project identity
- Claude Code and Cursor events carry `workspace_ref`, a keyed hash of the working directory, so "same project" is known without storing a path.
- It appears on traces, procedural executions and run memory.

## API
- `GET/PUT /v1/agent-brief`, `GET /v1/agent-brief/preview`, `POST /agent-brief/v1/brief` (brief token only).

This slice does not publish a separate v0.103 release; the completed stack publishes as v0.107.0.
