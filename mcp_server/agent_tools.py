from __future__ import annotations

from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from . import secure_runtime


_REGISTERED_MCP_IDS: set[int] = set()


def _bounded(value: int, *, minimum: int = 1, maximum: int) -> int:
    return min(max(minimum, int(value)), maximum)


def _clarify_work_summary(value: Any) -> Any:
    """Preserve legacy summary fields while making unknown test results explicit.

    A zero in runs_with_failures means no *known* failing run in the compact
    structural summary. It must not be read as proof that every run passed when
    the latest observed test result is unknown.
    """
    if not isinstance(value, dict):
        return value
    summary = dict(value)
    tests = summary.get("tests")
    if not isinstance(tests, dict):
        return summary
    out = dict(tests)
    runs = max(0, int(out.get("runs") or 0))
    known_failing = max(0, min(runs, int(out.get("runs_with_failures") or 0)))
    latest = str(out.get("ended") or "unknown")
    unknown = 1 if runs and latest == "unknown" else 0
    out.update({
        "known_failing": known_failing,
        "known_passing": max(0, runs - known_failing - unknown),
        "unknown_result": unknown,
        "zero_failures_does_not_mean_all_passed": unknown > 0,
    })
    summary["tests"] = out
    return summary


def _clarify_execution(execution: Any) -> Any:
    if not isinstance(execution, dict):
        return execution
    out = dict(execution)
    if "work_summary" in out:
        out["work_summary"] = _clarify_work_summary(out.get("work_summary"))
    return out


def _run_summary(execution: dict[str, Any]) -> dict[str, Any]:
    """Keep list responses compact while preserving interpretation boundaries."""
    compact = {
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
    if "work_summary" in compact:
        compact["work_summary"] = _clarify_work_summary(compact.get("work_summary"))
    return compact


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
        interpreted as zero underlying activity. Test summaries additionally
        expose known_passing/known_failing/unknown_result semantics so zero known
        failures is not mistaken for proof that every test run passed. Native
        run/trace/span IDs, prompts, model-response content, raw tool arguments/
        results and hidden reasoning are not exposed.
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
            "count_semantics": "prefer observed_operation_counts; interpret counts together with signal_capabilities; test unknown_result is not a pass",
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
        lifecycle surface; false coverage flags mean only not observed. Unknown
        test outcomes remain explicitly unknown rather than being counted as pass.
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
        if isinstance(result, dict) and isinstance(result.get("executions"), list):
            result = {**result, "executions": [_clarify_execution(item) for item in result["executions"]]}
        return core._finish(name, result)


__all__ = ["register_agent_tools"]
