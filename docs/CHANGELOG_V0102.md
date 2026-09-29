# OpenWorkGraph (unreleased, planned v0.102): did the work hold up?

Builds on v0.101. Until now a run's outcome was the agent's own end status. Claude's Stop, for example, means the turn ended, not that the work was right. This release adds what happened to the work.

## Outcome tracking (off by default)
- **Turning it on:** the History tab card "Did agent work hold up?".
- **What is watched:** pull requests an agent opens, via `gh pr create` or a create-pull-request tool, from Claude Code, Cursor or Codex.
  - Kept: only host, owner, repository and number, in a local watch list keyed to the run.
  - Never kept in events, never synced.
- **What is checked:** every 10 minutes, merged/closed state and CI, through your local `gh` login (read-only).
- **What the run gets:** a content-free `delivery_outcome`: PRs opened, merged, closed unmerged, open, and CI passing/pending/failing.
- **Where it shows:**
  - agent traces and MCP `get_agent_runs`;
  - run memory, updated even after the session's raw history is gone;
  - procedural-memory family summaries (`delivery`: PRs, merged, closed unmerged, runs with failing CI);
  - the History list ("PR merged, CI passing").
- **When links go:** a link is dropped when the PR resolves or after 30 days. Turning tracking off deletes every stored link, and deleting a session or date range deletes its watches.
- **Errors:** stored as codes only. A missing or logged-out `gh` is shown in the card.

## API
- `GET /v1/outcome-tracking`, `PUT /v1/outcome-tracking` (`enabled`), `POST /v1/outcome-tracking/check-now`.

## Not yet
- Reverts of merged work.
- Commits pushed without a pull request.
- Human rework after a run: that comes with the human↔agent join.
