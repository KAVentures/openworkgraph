from __future__ import annotations

"""Central saved-history permission guard for MCP transports.

Local retention and AI disclosure are independent. The guard wraps the existing
secure local API transport so old and new MCP tools cannot bypass a user's saved-
history choice merely because a particular endpoint predates History settings.
"""

from datetime import datetime
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError


_FULL_HISTORY_PATHS = {
    "/v1/events",
    "/v1/context-events",
    "/v1/operational-events",
}
_RANGELESS_HISTORICAL_PREFIXES = (
    "/v1/procedural-memory",
    "/v1/run-memory",
    "/v1/playbooks/local",
    "/v1/playbooks/export",
    "/v1/task-context",
)
_SESSION_PREFIXES = (
    "/v1/sessions/",
    "/v1/context-sessions/",
    "/v1/operational-sessions/",
)
_SCOPE_PATHS = {
    "/v1/tasks",
    "/v1/summary",
    "/v1/operational-summary",
    "/v1/semantic-activity",
    "/v1/operational-semantic-activity",
    "/v1/work-profile",
    "/v1/patterns",
}


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed
    except Exception:
        return None


def _iso_max(left: str | None, right: str | None) -> str | None:
    a, b = _parse(left), _parse(right)
    if a is None:
        return right
    if b is None:
        return left
    return (a if a >= b else b).isoformat()


def _iso_min(left: str | None, right: str | None) -> str | None:
    a, b = _parse(left), _parse(right)
    if a is None:
        return right
    if b is None:
        return left
    return (a if a <= b else b).isoformat()


def install_history_guard(runtime_module: Any) -> None:
    if getattr(runtime_module, "_owg_history_guard_installed", False):
        return
    original_get = runtime_module.secure_get

    def access() -> dict[str, Any]:
        try:
            payload = original_get("/v1/history/ai-access")
            value = payload.get("access") if isinstance(payload, dict) else None
            return dict(value) if isinstance(value, dict) else {"mode": "off"}
        except Exception:
            return {"mode": "off"}

    def current_start() -> str | None:
        try:
            status = original_get("/v1/capture/status")
            return str(status.get("run_started_at") or "") or None
        except Exception:
            return None

    def require_all_saved(path: str) -> None:
        if str(access().get("mode") or "off") != "all_saved":
            raise ToolError(
                "Saved-history AI access does not cover this aggregate. In OpenWorkGraph History, grant "
                "All saved history, or use list_history/get_workflow_trace/get_agent_runs with an allowed date range."
            )

    def bounded_range(params: dict[str, Any], *, use_current_when_off: bool) -> dict[str, Any]:
        result = dict(params)
        lease = access()
        mode = str(lease.get("mode") or "off")
        if mode == "all_saved":
            return result
        if mode == "selected_range":
            result["since"] = _iso_max(str(result.get("since") or "") or None, str(lease.get("since") or "") or None)
            result["until"] = _iso_min(str(result.get("until") or "") or None, str(lease.get("until") or "") or None)
            start, end = _parse(result.get("since")), _parse(result.get("until"))
            if start is not None and end is not None and end <= start:
                raise ToolError("The requested history range is outside the saved-history range you allowed.")
            return result
        if not use_current_when_off:
            raise ToolError("Saved-history AI access is OFF. Grant a date range in OpenWorkGraph History first.")
        start = current_start()
        if not start:
            raise ToolError("OpenWorkGraph could not establish the current recording boundary; saved-history access remains off.")
        result["since"] = _iso_max(str(result.get("since") or "") or None, start)
        return result

    def guarded_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        route = str(path or "")
        query = dict(params or {})

        if route == "/v1/history":
            query = bounded_range(query, use_current_when_off=False)
        elif route == "/v1/workflow-trace":
            # Cursor state must never restore dates from a broader prior grant.
            query = bounded_range(query, use_current_when_off=True)
        elif route == "/v1/agent-execution-traces":
            query = bounded_range(query, use_current_when_off=True)
        elif route.startswith("/v1/workflow-evidence"):
            # Skill/procedure evidence may use the current run without saved-history
            # access. Older evidence is bounded to exactly the range the person
            # granted, including derived-family discovery.
            query = bounded_range(query, use_current_when_off=True)
        elif route in _SCOPE_PATHS:
            scope = str(query.get("scope") or "current")
            if scope != "current":
                require_all_saved(route)
        elif route in _FULL_HISTORY_PATHS or route.startswith(_RANGELESS_HISTORICAL_PREFIXES) or route.startswith(_SESSION_PREFIXES):
            require_all_saved(route)

        return original_get(route, query if query else None)

    runtime_module.secure_get = guarded_get
    # Legacy tools call the transport through mcp_server.main's injected hook.
    if hasattr(runtime_module, "core"):
        runtime_module.core._get = guarded_get
    runtime_module._owg_history_guard_installed = True


__all__ = ["install_history_guard"]
