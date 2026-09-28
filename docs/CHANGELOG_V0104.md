# OpenWorkGraph (unreleased, planned v0.104): human work joined to agent runs

Builds on v0.103. Human capture and agent observation were recorded side by side but never connected. Each agent run now says what the person did around it.

## What the person did during and after a run
- **Measures:** traces, run memory and MCP run lists gain `human_context`:
  - `during` the run: engaged and away seconds, by app category (terminal, editor, browser, communication, AI assistant, other);
  - `after` it: the same measures until the next turn in the session (at most 2 hours), plus the gap.
- **No capture is not zero:** without human capture in a window, it says `human_capture_observed: false`.
- **Subagents:** runs inside a turn are not treated as the "next turn".

## Did the person rework the agent's files?
- **How it works:** the Claude Code hook snapshots the project's changed files, as keyed hashes of paths and contents, when a turn ends, and compares at the next prompt. The next turn records only counts (`between_turns`):
  - files changed;
  - how many of them the agent had edited in the last 24 hours;
  - whether HEAD moved (a commit, pull or checkout is reported as that, not guessed at);
  - the gap.
- **Where it shows:** it is attributed to the run it followed (`human_context.after.between_turns`), shown in History ("you changed 2 of its files after"), and summarized in session briefs ("changed files the agent had just edited in 3 of 10 turns; median time to the next prompt: 4 min").
- **Safety:**
  - runs only under a recording lease;
  - git's fsmonitor is disabled, so a repository cannot make it run commands (tested);
  - it never takes git's index lock;
  - there is a 2 s timeout;
  - it is bounded to 200 files and 50 MB;
  - its state is local, keyed by the project's hash, and expires after 7 days.
- **Off switch:** `OWG_AGENT_REWORK=0`.

## Not yet
- Rework after the *last* turn of a session is only compared when the next session in the same project starts. It is recorded, but not yet attributed back across sessions.
