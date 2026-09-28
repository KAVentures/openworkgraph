# OpenWorkGraph (unreleased, planned v0.100): richer agent work evidence

Builds on v0.99. Agent runs now say what the agent actually did, not only which tools it called. The privacy model is unchanged: structure only, no content.

## What each tool call did (content-free)
- **Derived in memory, per tool call:** Claude Code and Cursor hooks and Codex's log events carry a tool's input and output. From them OpenWorkGraph now derives a few structural facts, and nothing else:
  - which well-known programs ran (`pytest`, `git`, `npm`), from a fixed allowlist;
  - git and GitHub CLI operations (`commit`, `push`, `pr_create`);
  - test pass/fail counts from a recognised runner summary (pytest, jest, vitest, cargo, node --test, unittest);
  - file types, keyed hashes of file paths, and lines added/removed.
- **What is never stored:** command text, arguments, paths, file names, contents and output.
- **One gate:** every value passes one allowlist gate again when it is stored. Quoted text, such as a commit message or an `echo` string, never counts as a command.
- **File refs:** keyed with a secret that never leaves the computer. They show "the same file again" and cannot be compared across devices.
- **Off switch:** `OWG_AGENT_TOOL_DETAIL=0`.

## Run work summary
- **What it holds:** each agent run in the traces (and MCP `get_agent_runs`) has a `work_summary`:
  - commands used;
  - git/gh operations;
  - test runs, and whether the run ended with tests passing or failing;
  - files edited vs only read;
  - file types, lines changed and total tokens.
- **Links:** run lists also carry `parent_execution_id` / `child_execution_ids`. The compact MCP view carries `usage_totals` and `models_observed`.

## Codex token usage
- **Before:** Codex reports tokens on `response.completed` SSE events, not on API requests, so Codex runs showed no token usage.
- **Now:** each completed response is one model call with input, output, cached and total tokens. A successful API request no longer adds a second, empty model call. A failed attempt is still a model call with status error.
- **Duplicate copies:** Codex sends each event as both a log record and a trace span event, and only the log copy has the tool's arguments and output. Whichever copy is stored first, the later one may fill in missing tool detail or token usage. It never overwrites a stored value.

## Claude Code subagents are their own runs
- **Before:** a subagent's tool calls merged into the parent turn.
- **Now:** hooks fired inside a subagent (they carry `agent_id`) form a child run. SubagentStart/SubagentStop open and close it, and the handoff links it to the parent turn.
- **Stable IDs:** the child run ID uses only the session and the subagent ID, so it does not depend on the subagent's hooks carrying the parent's `prompt_id`.
- **Ephemeral history:** a subagent finishing does not purge the session.

## Deliberately not in this release
- **Claude Code OTel metrics** (commits, PRs, lines of code): commits, PRs and lines are now derived structurally for every hook-based agent. A second, Claude-only metrics feed would count the same work twice.
- **PreCompact:** still not a run boundary.
