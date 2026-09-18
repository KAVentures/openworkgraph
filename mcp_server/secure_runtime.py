from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp.server.mcpserver.exceptions import ToolError

from server.local_auth import ensure_api_token
from . import main as core

API_URL = os.getenv("WORKFLOW_OBSERVER_API", "http://127.0.0.1:8787").rstrip("/")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ensure_api_token()}"}


def secure_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=15, headers=_headers()) as client:
        response = client.get(f"{API_URL}{path}", params=params)
        response.raise_for_status()
        return response.json()


def secure_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=15, headers=_headers()) as client:
        response = client.post(f"{API_URL}{path}", json=payload)
        response.raise_for_status()
        return response.json()


def _activity_summary(result: dict[str, Any]) -> dict[str, Any]:
    timestamps: list[str] = []
    row_count = 0

    def walk(value: Any) -> None:
        nonlocal row_count
        if isinstance(value, dict):
            observed = value.get("observed_at")
            if isinstance(observed, str) and observed:
                timestamps.append(observed)
            for key, child in value.items():
                if key in {"rows", "events", "tasks", "examples", "candidates"} and isinstance(child, list):
                    row_count += len(child)
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(result)
    try:
        size = len(json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except Exception:
        size = 0
    timestamps.sort()
    return {
        "rows": row_count,
        "bytes": size,
        "range_start": timestamps[0] if timestamps else "",
        "range_end": timestamps[-1] if timestamps else "",
    }


def authorize_tool(tool_name: str) -> None:
    try:
        state = secure_get("/v1/ai-access")
    except Exception as exc:
        raise ToolError("OpenWorkGraph could not verify AI access. Keep OpenWorkGraph running and reopen its local dashboard.") from exc
    if state.get("enabled"):
        return
    try:
        secure_post("/v1/mcp-activity", {"tool": tool_name, "status": "denied", "rows": 0, "bytes": 0})
    except Exception:
        pass
    raise ToolError("OpenWorkGraph AI access is OFF. Enable AI access in the local dashboard for this run.")


def audit_tool(tool_name: str, result: dict[str, Any]) -> None:
    try:
        summary = _activity_summary(result)
        secure_post("/v1/mcp-activity", {"tool": tool_name, "status": "ok", **summary})
    except Exception:
        # Observability must never turn a successful evidence read into a failure.
        pass


# Existing MCP tools resolve these globals at call time. Swap only the local API
# transport/access hooks; tool definitions and prompt-injection filtering stay in
# mcp_server.main.
core._get = secure_get
core._authorize_tool = authorize_tool
core._audit_tool = audit_tool
mcp = core.mcp
