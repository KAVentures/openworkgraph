# OpenWorkGraph v0.107 stack slice: human work joined to agent runs

This unreleased slice builds on the session-brief stack. Human capture and agent observation were recorded side by side but never connected; each agent run can now describe what the person did around it without adding content capture.

## What the person did during and after a run
- **Measures:** traces, run memory and MCP run lists gain `human_context`:
  - `during` the run: engaged and away seconds, by app category (terminal, editor, browser, communication, AI assistant, other);
  - `after` it: the same measures until the next turn in the session (at most 2 hours), plus the gap.
- **No capture is not zero:** without human capture in a window, it says `human_capture_observed: false`.
- **Subagents:** runs inside a turn are not treated as the "next turn".

## Did the person rework the immediately preceding agent turn?
- **How it works:** the Claude Code hook tracks file refs edited in the current turn, snapshots the project's changed files as keyed hashes of paths and contents when that turn ends, and compares at the next prompt.
- `between_turns.agent_files_changed` counts only changed files the **immediately preceding turn** edited. Older edits are not carried forward through a rolling 24-hour set, avoiding false attribution to a later turn.
- The next turn records only counts: files changed, immediately-prior-turn overlap, whether HEAD moved, and the gap.
- **Where it shows:** it is attributed to the run it followed (`human_context.after.between_turns`), shown in History and summarized in session briefs.
- **Cross-platform correctness:** concurrent async Claude hooks are serialized with an atomic lock file that works on Windows, macOS and Linux. Lock ownership is tokenized, stale locks recover, and cleanup never removes another process's active lock.
- **Safety:**
  - runs only under a recording lease;
  - git's fsmonitor is disabled, so a repository cannot make it run commands;
  - it never takes git's index lock;
  - git calls have a 2 s timeout;
  - snapshots are bounded to 200 files and 50 MB;
  - state is local, keyed by the project's hash, and expires after 7 days.
- **Off switch:** `OWG_AGENT_REWORK=0`.

## Verification
The regression suite includes a real temporary git repository, fsmonitor trap, symlink handling, sanitization, human-context joining, previous-turn-only attribution and a separate Python process that holds the workspace lock so Windows/macOS/Linux CI exercise cross-process contention.

This slice does not publish a separate v0.104 release; the completed stack publishes as v0.107.0.
