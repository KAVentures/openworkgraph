# OpenWorkGraph v0.107 stack slice: run memory

This change is an unreleased slice of the v0.107 stack, built on v0.100. Ephemeral raw history otherwise leaves repeated-workflow and similar-run features nothing to learn from after a session ends; run memory preserves a small structural summary without keeping work content.

## Run memory
- **What is kept:** one small, content-free record per run, taken just before retention removes the session (ephemeral close, expiry or crash recovery):
  - hashed family and structural steps;
  - the agent's reported end status and timing;
  - approval points;
  - for agents, the work summary: commands, git/gh, test outcome, file and line counts, tokens.
- **What is never kept:** titles, URLs, paths, prompts or tool content. Native session/run IDs are never stored in plaintext; persistent linkage uses local-key HMACs, while public execution references remain opaque one-way hashes.
- **New-install default:** on for 90 days, disclosed in History and independently switchable/deletable.
- **Upgrade behavior:** an existing policy created before run memory where both human and agent history were explicitly `ephemeral` migrates with run memory **off**, so an old "don't keep after session" choice is not silently widened. Existing users already retaining some history keep the disclosed run-memory default.
- **Deletion wins:** deleting a session or a date range yourself deletes its memory too. A run that exists only in memory can be deleted from History by its execution ID.
- **Used by:** `find_repeated_workflows`, `how_did_similar_runs_go` and the procedural-memory API, with `"source": "run_memory"`. A run still in raw history is always derived from the raw evidence, never duplicated.
- **Local only:** memory never leaves this computer (the Gateway connector syncs only events). MCP reads it only under "All saved history" AI access.

## API
- `GET /v1/run-memory`, `PUT /v1/run-memory/policy` (`enabled`, `days`), `POST /v1/run-memory/forget` (`execution_id` or `all`).
- `GET /v1/history-policy` includes `run_memory` and explains the tradeoff.
- Session deletion, range deletion and cleanup results report `run_memory_kept`, `run_memory_deleted` and `run_memory_pruned`.

This slice does not publish a separate v0.101 release; the completed stack publishes as v0.107.0.
