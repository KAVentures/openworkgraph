from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server import MCPServer

API_URL = os.getenv("WORKFLOW_OBSERVER_API", "http://127.0.0.1:8787").rstrip("/")
mcp = MCPServer("Workflow Observer")


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    with httpx.Client(timeout=15) as client:
        r = client.get(f"{API_URL}{path}", params=params)
        r.raise_for_status()
        return r.json()


@mcp.tool()
def company_workflow_summary(max_events: int = 10000) -> dict[str, Any]:
    """Return an evidence-based summary of observed work: work surfaces, effort, transitions, semantic events, candidate tasks and repeated task patterns."""
    return _get("/v1/operational-summary", {"limit": max_events, "scope": "current"})


@mcp.tool()
def search_work_observations(query: str = "", app_name: str | None = None, limit: int = 100) -> dict[str, Any]:
    """Search privacy-safe normalized work events by surface/action. Raw titles and content are not exposed through MCP."""
    params: dict[str, Any] = {"query": query, "limit": limit}
    if app_name:
        params["surface"] = app_name
    return _get("/v1/operational-events", params)


@mcp.tool()
def recent_semantic_activity(limit: int = 200) -> dict[str, Any]:
    """Return recent semantic work actions from browser instrumentation and desktop accessibility, without typed field values."""
    return _get("/v1/operational-semantic-activity", {"limit": limit, "scope": "current"})


@mcp.tool()
def candidate_task_executions(max_events: int = 25000) -> dict[str, Any]:
    """Return candidate task executions with duration, overlap-aware effort, work surfaces, semantic evidence and explicit boundary reasons. Labels are suggestions and require review."""
    return _get("/v1/tasks", {"limit": max_events, "scope": "current"})


@mcp.tool()
def get_work_session(session_id: str, limit: int = 1000) -> dict[str, Any]:
    """Return the chronological privacy-safe operational trace for one observed work session."""
    return _get(f"/v1/operational-sessions/{session_id}", {"limit": limit})


@mcp.tool()
def automation_candidates(max_events: int = 25000) -> dict[str, Any]:
    """Return repeated candidate tasks and workflow fragments for automation analysis. These are evidence signals, not a claim that automation is safe or appropriate."""
    data = _get("/v1/operational-summary", {"limit": max_events, "scope": "current"})
    candidates = []
    for item in data.get("repeated_task_patterns", []):
        candidates.append({
            "candidate_task": item.get("suggested_label"),
            "observed_count": item.get("observed_count", 0),
            "surfaces": item.get("surfaces", []),
            "total_engaged_seconds": item.get("total_engaged_seconds", 0),
            "median_engaged_seconds": item.get("median_engaged_seconds", 0),
            "p90_engaged_seconds": item.get("p90_engaged_seconds", 0),
            "surface_variant_count": item.get("surface_variant_count", 1),
            "completion_boundary_count": item.get("completion_boundary_count", 0),
            "keypress_count": item.get("keypress_count", 0),
            "click_count": item.get("click_count", 0),
            "confidence": item.get("confidence", "low"),
            "reason": "Repeated heuristic task signature with observed manual effort",
            "needs_human_review": True,
        })
    if not candidates:
        for item in data.get("frequent_sequences", []):
            sequence = item.get("sequence", [])
            count = int(item.get("count", 0))
            if count >= 2:
                candidates.append({
                    "sequence": sequence,
                    "observed_count": count,
                    "reason": "Repeated cross-surface sequence observed multiple times",
                    "needs_human_review": True,
                })
    return {"candidates": candidates[:30], "source_events": data.get("events", 0)}


@mcp.resource("workflow-observer://data-model")
def data_model() -> str:
    """Explain what the Workflow Observer dataset contains and what it deliberately does not capture."""
    return """OpenWorkGraph v25 uses two local data layers. The raw evidence layer preserves rich customer-owned context for local verification and debugging. A separate normalized operational layer derives work-surface identity, generic actions, timing/effort and task structure while omitting arbitrary visible text, typed values, full URL paths, subjects, names, document titles and record identifiers. MCP tools use the normalized operational layer by default. Keyboard telemetry is aggregate counts/timing only: key identities, key order and typed text are never stored. Candidate task executions and repeated task patterns are heuristic observational evidence with suggested labels and explicit boundary reasons; they require contextual review before automation decisions."""


if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.getenv("MCP_HOST", "127.0.0.1"),
            port=int(os.getenv("MCP_PORT", "8788")),
            stateless_http=True,
            json_response=True,
        )
    else:
        mcp.run()
