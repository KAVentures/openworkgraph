from __future__ import annotations

from typing import Any
from mcp.types import ToolAnnotations


def register_history_tools(mcp: Any, runtime: Any) -> None:
    if getattr(mcp, "_owg_history_tools_registered", False):
        return

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    def list_history(
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """List retained human/agent sessions before drilling into canonical evidence.

        Saved-history access is user-controlled and time-limited. This tool returns
        factual session metadata only: dates, activity blocks, surfaces, durations,
        retention state and observed agent coverage. It does not infer task names.
        After selecting relevant dates, use get_workflow_trace or get_agent_runs.
        """
        name = "list_history"
        runtime.core._begin(name)
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 500))}
        if since: params["since"] = since
        if until: params["until"] = until
        result = runtime.secure_get("/v1/history", params)
        result["next_step"] = "Use get_workflow_trace for canonical human/work evidence and get_agent_runs for structural agent evidence in the relevant dates."
        result["task_labels_inferred"] = False
        return runtime.core._finish(name, result)

    setattr(mcp, "_owg_history_tools_registered", True)


__all__ = ["register_history_tools"]
