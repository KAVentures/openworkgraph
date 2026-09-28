# OpenWorkGraph (unreleased, planned v0.103): session-start briefs

Builds on v0.102. Everything OpenWorkGraph learned could only be pulled by an agent through MCP, and saved-history AI access resets to off on every restart. Now the loop closes: an agent can start each session knowing how past work in the same project went.

## Brief agents at session start (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → Claude Code. This adds one synchronous `SessionStart` hook, separate from the observation hooks. Turning it off removes only that hook.
- **When it runs:** new, cleared and compacted sessions (not resumed ones).
- **What the brief holds:**
  - scope: runs in the same project, or across projects when this one has no history yet, over the past 30 days;
  - how tests ended and the most recent result;
  - pull request outcomes (with outcome tracking);
  - the commands used most;
  - the typical files and lines per run;
  - median tokens.
- **Where the facts come from:** raw history and run memory. It works with "Don't keep after session".
- **What it never holds:** titles, paths, prompts or content. It is at most 900 characters and labeled observational, not instructions.
- **Preview:** "Preview brief" shows exactly what an agent would receive. The card counts every brief sent.
- **Token:** the hook uses its own brief-only token. The write-only ingest token still cannot read anything.
- **Failure:** the hook fails open. After 2 seconds or on any error it adds nothing.

## Project identity
- Claude Code and Cursor events now carry `workspace_ref`, a keyed hash of the working directory, so "same project" is known without storing a path.
- It appears on traces, procedural executions and run memory.

## API
- `GET/PUT /v1/agent-brief`, `GET /v1/agent-brief/preview`, `POST /agent-brief/v1/brief` (brief token only).
