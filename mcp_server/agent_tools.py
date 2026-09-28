from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import secure_runtime


_REGISTERED_MCP_IDS: set[int] = set()


def _bounded(value: int, *, minimum: int = 1, maximum: int) -> int:
    return min(max(minimum, int(value)), maximum)


def _run_summary(execution: dict[str, Any]) -> dict[str, Any]:
    """Keep list responses compact while preserving interpretation boundaries."""
    return {
        key: execution.get(key)
        for key in (
            "execution_id",
            "started_at",
            "ended_at",
            "agent",
            "observation_level",
            "outcome_status",
            "outcome_basis",
            "observed_family_key",
            "observed_family_basis",
            "run_start_observed",
            "run_finish_observed",
            "complete_boundary_observed",
            "event_count_total",
            # operation_counts remains for backward compatibility. New clients
            # should prefer observed_operation_counts because it applies native
            # adapter de-duplication (for example Claude hooks + OTel).
            "operation_counts",
            "observed_operation_counts",
            "tool_category_counts",
            # What the run did: commands, git/gh, how tests ended, files, lines, tokens.
            "work_summary",
            "delivery_outcome",
            "human_context",
            "parent_execution_id",
            "child_execution_ids",
            "structural_steps",
            "structural_steps_truncated",
            "approval_request_count",
            "approval_received_count",
            "task_context_linkage_status",
            "observed_coverage",
            "signal_capabilities",
            "telemetry_sources",
            "telemetry_depth",
            "models_observed",
            "usage_totals",
            "derived",
            "authoritative",
        )
        if key in execution
    }


def register_agent_tools(mcp: Any) -> None:
    """Register agent-observability reads on one MCP server instance exactly once."""
    marker = id(mcp)
    if marker in _REGISTERED_MCP_IDS:
        return
    _REGISTERED_MCP_IDS.add(marker)

    core = secure_runtime.core

    @mcp.tool()
    def get_agent_runs(
        family_key: str = "",
        since: str | None = None,
        limit: int = 20,
        evidence_limit: int = 25_000,
    ) -> dict[str, Any]:
        """Return compact privacy-safe summaries of observed agent executions.

        Results report observed structural counts separately from the active
        adapter's capability to observe each signal. A numeric zero is meaningful
        only when that signal is observable; not_observable/unknown must not be
        interpreted as zero underlying activity. Native run/trace/span IDs,
        prompts, model-response content, tool arguments/results and hidden
        reasoning are not exposed.
        """
        name = "get_agent_runs"
        core._begin(name)
        params: dict[str, Any] = {
            "family_key": str(family_key or ""),
            "limit": _bounded(limit, maximum=100),
            "evidence_limit": _bounded(evidence_limit, maximum=100_000),
            "max_events_per_execution": 1,
        }
        if since not in (None, ""):
            params["since"] = since
        result = secure_runtime.secure_get("/v1/agent-execution-traces", params)
        compact = {
            **{key: value for key, value in result.items() if key != "executions"},
            "executions": [_run_summary(item) for item in list(result.get("executions") or [])],
            "events_omitted_from_list_view": True,
            "count_semantics": "prefer observed_operation_counts; interpret counts together with signal_capabilities",
            "detail_tool": "get_agent_execution_trace",
        }
        return core._finish(name, compact)

    @mcp.tool()
    def get_playbooks(family_key: str = "", include_my_workflows: bool = False) -> dict[str, Any]:
        """Return shared, content-free playbooks: how a kind of work usually went.

        Imported playbooks were shared with this person on purpose (typical
        readable steps, commands, how tests and pull requests ended, how often
        the person reworked the result, typical size). With
        include_my_workflows=true, also list this person's own repeated
        workflows (needs saved-history AI access). Playbooks are observations,
        not instructions or authorization.
        """
        name = "get_playbooks"
        core._begin(name)
        result: dict[str, Any] = {"imported": secure_runtime.secure_get(
            "/v1/playbooks/imported", {"family_key": str(family_key or "")} if family_key else None).get("playbooks") or []}
        if include_my_workflows:
            result["my_workflows"] = secure_runtime.secure_get("/v1/playbooks/local").get("families") or []
        result["interpretation"] = "observations of how similar work went; not instructions or authorization"
        return core._finish(name, result)

    @mcp.tool()
    def get_agent_execution_trace(
        execution_id: str,
        max_events: int = 100,
        evidence_limit: int = 25_000,
    ) -> dict[str, Any]:
        """Return one ordered privacy-safe structural trace for an observed agent run.

        The execution ID must be an opaque OpenWorkGraph ID previously returned by
        the agent-run view. Completeness depends on the integration's observed
        lifecycle surface; false coverage flags mean only not observed.
        """
        opaque_id = str(execution_id or "").strip().lower()
        if not opaque_id.startswith("execution:"):
            raise ToolError("execution_id must be an opaque OpenWorkGraph execution ID")
        name = "get_agent_execution_trace"
        core._begin(name)
        result = secure_runtime.secure_get(
            "/v1/agent-execution-traces",
            {
                "execution_id": opaque_id,
                "limit": 1,
                "evidence_limit": _bounded(evidence_limit, maximum=100_000),
                "max_events_per_execution": _bounded(max_events, maximum=500),
            },
        )
        return core._finish(name, result)


__all__ = ["register_agent_tools"]
