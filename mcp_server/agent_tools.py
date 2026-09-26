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
            "operation_counts",
            "tool_category_counts",
            "structural_steps",
            "structural_steps_truncated",
            "approval_request_count",
            "approval_received_count",
            "task_context_linkage_status",
            "observed_coverage",
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

        Results report only structural signals actually present in canonical
        evidence. Missing coverage means not observed, not proof that the runtime
        did not perform the underlying action. Native run/trace/span identifiers,
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
            "detail_tool": "get_agent_execution_trace",
        }
        return core._finish(name, compact)

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
