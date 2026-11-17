from __future__ import annotations

"""Read-only process-intelligence MCP tools over event logs the owner shared with agents.

Listed only once the owner shares at least one saved event log from the dashboard; each
call still needs the AI-access switch, and each log answers only at the level it was shared.
"""
import re
from typing import Any

from mcp.types import ToolAnnotations
from mcp.server.mcpserver.exceptions import ToolError

_LOG_ID = re.compile(r"^log_[0-9a-f]{16}$")
_OUTCOMES = {"duration_over", "rework", "contains_activity", "ends_with"}
_REGISTERED: set[int] = set()
_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_NOTE = ("Describes recorded events in an owner-shared event log. Durations are elapsed time between recorded "
         "events; associations are not causes. Nothing here authorizes an action or changes the log.")


def register_process_intelligence_tools(mcp: Any, runtime_module: Any) -> None:
    sid = id(mcp)
    if sid in _REGISTERED:
        return
    _REGISTERED.add(sid)

    def _get(tool: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        runtime_module.authorize_tool(tool)
        try:
            result = runtime_module.secure_get(path, {k: v for k, v in (params or {}).items() if v is not None})
        except Exception as exc:  # the server's message (not shared, invalid input) is the useful answer
            raise ToolError(str(exc) or "process intelligence request failed") from None
        result["interpretation"] = _NOTE
        return runtime_module.core._finish(tool, result)

    def _log(log_id: str) -> str:
        if not _LOG_ID.fullmatch(str(log_id or "")):
            raise ToolError("Use a log_id returned by list_process_logs.")
        return log_id

    @mcp.tool(annotations=_READ_ONLY)
    def list_process_logs() -> dict[str, Any]:
        """List the saved process event logs the owner shared with agents.

        Each entry has a log_id, name, sharing level (aggregates or cases), size and its case
        notions (object types). Use a log_id from here with the other process tools. Logs the
        owner did not share are never listed.
        """
        return _get("list_process_logs", "/v1/process-intelligence/logs")

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_overview(log_id: str, case_type: str | None = None, sla_hours: float | None = None) -> dict[str, Any]:
        """Overview of a shared process: case counts, durations, KPIs, top variants and automatic insights.

        Use when asked how a process performs, where time goes or what to look at first.
        case_type picks the case notion (object type) for object-centric logs; sla_hours adds
        SLA breach rates. Insights include a case filter (cohort) describing their cases.
        """
        return _get("get_process_overview", f"/v1/process-intelligence/{_log(log_id)}/overview",
                    {"case_type": case_type, "sla_hours": sla_hours})

    @mcp.tool(annotations=_READ_ONLY)
    def get_process_root_cause(log_id: str, outcome: str, hours: float | None = None,
                               activity: str | None = None, case_type: str | None = None) -> dict[str, Any]:
        """Which activities, resources and case attributes are associated with an outcome.

        outcome is duration_over (needs hours), rework, contains_activity or ends_with (both need
        activity). Returns risk differences with confidence intervals and a Bonferroni flag.
        These are associations in the log, never causes.
        """
        if outcome not in _OUTCOMES:
            raise ToolError("outcome must be duration_over, rework, contains_activity or ends_with")
        return _get("get_process_root_cause", f"/v1/process-intelligence/{_log(log_id)}/root-cause",
                    {"outcome": outcome, "hours": hours, "activity": activity, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def explain_process_case(log_id: str, case_id: str, case_type: str | None = None) -> dict[str, Any]:
        """Explain one case: its steps, which gaps took longer than usual for that transition,
        repeated and uncommon steps, and how its duration compares with the log.

        Needs a log shared at the cases level. Use a case_id from get_slowest_process_cases or
        one the user named; never guess identifiers.
        """
        if not isinstance(case_id, str) or not 0 < len(case_id) <= 500:
            raise ToolError("case_id must be a non-empty string of at most 500 characters")
        return _get("explain_process_case", f"/v1/process-intelligence/{_log(log_id)}/case",
                    {"case_id": case_id, "case_type": case_type})

    @mcp.tool(annotations=_READ_ONLY)
    def get_slowest_process_cases(log_id: str, limit: int = 10, case_type: str | None = None) -> dict[str, Any]:
        """The slowest cases of a shared log (first to last recorded event), slowest first.

        Needs a log shared at the cases level. Follow up with explain_process_case.
        """
        return _get("get_slowest_process_cases", f"/v1/process-intelligence/{_log(log_id)}/slowest-cases",
                    {"limit": max(1, min(int(limit), 50)), "case_type": case_type})


__all__ = ["register_process_intelligence_tools"]
