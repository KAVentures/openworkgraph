# OpenWorkGraph v0.87.1

Bugfix release for the compact MCP feedback loop introduced in v0.87.0.

- `find_repeated_workflows` now exposes exact procedural-memory `family_key` values alongside display `task_family` values when an exact mapping exists.
- `how_did_similar_runs_go` accepts exact family keys and bare canonical human task families, resolves only unambiguous current human families when no key is supplied, and returns explicit `family_selection_required` / `unknown_family_key` states instead of silently empty history.
- Agent family identifiers are never fabricated from bare task-family strings; exact procedural-memory keys remain authoritative for lookup identity.
- Compact `get_current_work_context` and `get_workflow_trace` use smaller defaults while retaining explicit larger limits and stable pagination.
- `get_task_context` and feedback-tool descriptions now explain the expected inputs and discovery chain.
- `docs/EXPORTS_AND_AI.md` now points new local clients to `mcp_server.compact_stdio`; `mcp_server.secure_stdio` remains the legacy 24-tool compatibility entrypoint.

No capture schema, stored evidence, procedural-memory REST route, governance REST route, Gateway transport, or legacy MCP tool is removed by this patch.
