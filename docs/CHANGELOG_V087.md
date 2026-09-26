# OpenWorkGraph v0.87 MCP surface changes

v0.87 adds a compact MCP surface for **new** local AI connections while retaining the previous 24-tool stdio server for backward compatibility.

## New compact surface

```text
get_current_work_context
search_work
get_workflow_trace
get_work_profile
find_repeated_workflows
get_task_context
how_did_similar_runs_go
get_agent_runs
```

When `OWG_EXPERIMENTAL_GOVERNANCE=1`, the compact server additionally registers:

```text
get_action_policy_advisory
get_governed_context_pack
```

## Consolidation map

| Compact tool | Legacy capabilities represented |
|---|---|
| `get_current_work_context` | `get_current_work_context` + `recent_semantic_activity` |
| `search_work` | `search_work_history` + `search_work_observations` + `find_similar_work` |
| `get_workflow_trace` | `get_workflow_trace` + session filtering previously exposed through `get_context_session` / `get_work_session` |
| `get_work_profile` | unchanged |
| `find_repeated_workflows` | `candidate_task_executions` + `automation_candidates` + `find_process_examples` |
| `get_task_context` | unchanged task-context semantics and MCP representation provenance |
| `how_did_similar_runs_go` | `get_similar_runs` + `get_failure_patterns` + `get_next_likely_steps` + observational `get_approval_patterns` + `get_procedural_context_pack` |
| `get_agent_runs` | `get_agent_runs` + `get_agent_execution_trace` via optional `execution_id` |

`company_workflow_summary` is not exposed as a separate compact tool because it is a local current-run summary rather than a Gateway/company-specific capability. Current context and derived workflow views cover that use case without adding another overlapping tool choice.

## Compatibility

Existing configurations that launch:

```text
python -m mcp_server.secure_stdio
```

continue to expose the legacy 24-tool surface. No legacy MCP implementation is deleted or renamed.

New dashboard-generated configurations launch `mcp_server.compact_stdio`. The v0.87 Claude MCP bundle uses the same compact entrypoint, and the on-demand authenticated HTTP bridge uses `mcp_server.compact_http_app`.

## Interpretation boundaries

The consolidation does not change the evidence model:

- observed workflow repetition is not organizational policy;
- approval-request hotspots are observations, not permissions;
- human completion is not automatically a validated success;
- agent coverage remains partial when signals were not observed;
- prompts, model responses, tool arguments/results and hidden reasoning remain outside the agent evidence contract;
- MCP results continue to pass through the existing AI-access checks, audit path and prompt-injection protection.
