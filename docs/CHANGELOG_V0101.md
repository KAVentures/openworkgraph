# OpenWorkGraph (unreleased, planned v0.101): run memory

Builds on v0.100. New installs start with "Don't keep after session", which deleted every run once its session ended, so repeated-workflow and similar-run features had nothing to learn from. Run memory fixes that without keeping content.

## Run memory
- **What is kept:** one small, content-free record per run, taken just before retention removes the session (ephemeral close, expiry or crash recovery):
  - hashed family and structural steps;
  - the agent's reported end status and timing;
  - approval points;
  - for agents, the work summary: commands, git/gh, test outcome, file and line counts, tokens.
- **What is never kept:** titles, URLs, paths, prompts or tool content. Native session and run IDs are stored only as keyed hashes.
- **Default:** on, for 90 days. The History tab says so plainly, lists remembered runs, and lets you delete one, delete all, change the period, or turn memory off. Turning it off deletes all of it.
- **Deletion wins:** deleting a session or a date range yourself deletes its memory too. A run that exists only in memory can be deleted from History by its execution ID.
- **Used by:** `find_repeated_workflows`, `how_did_similar_runs_go` and the procedural-memory API, with `"source": "run_memory"`. A run still in raw history is always derived from the raw evidence, never duplicated.
- **Local only:** memory never leaves this computer (the Gateway connector syncs only events). MCP reads it only under "All saved history" AI access.

## API
- `GET /v1/run-memory`, `PUT /v1/run-memory/policy` (`enabled`, `days`), `POST /v1/run-memory/forget` (`execution_id` or `all`).
- `GET /v1/history-policy` now includes `run_memory` and explains the tradeoff.
- Session deletion, range deletion and cleanup results report `run_memory_kept`, `run_memory_deleted` and `run_memory_pruned`.
