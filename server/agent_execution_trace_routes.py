from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .agent_execution_traces import agent_execution_traces
from .agent_observability import enrich_agent_execution_payload
from .agent_read_auth import agent_read_authorized
from .agent_tool_labels import readable_tool_name
from .procedural_memory import load_recent_evidence

router = APIRouter()


def _require_agent_report_read(request: Request) -> None:
    if not agent_read_authorized(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API or dashboard authentication required")


def _row_framework(row: dict[str, Any]) -> str:
    if str(row.get("source") or "") != "agent": return ""
    meta = row.get("metadata")
    if meta is None and row.get("metadata_json"):
        try: meta = json.loads(row["metadata_json"])
        except (TypeError, ValueError): return ""
    agent = meta.get("agent") if isinstance(meta, dict) else None
    return str((agent or {}).get("framework") or "") if isinstance(agent, dict) else ""


def _parse(value: Any) -> datetime | None:
    try: return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except Exception: return None


def _sanitize_tool_names(payload: dict[str, Any]) -> dict[str, Any]:
    executions = payload.get("executions") if isinstance(payload, dict) else None
    if not isinstance(executions, list): return payload
    for execution in executions:
        if not isinstance(execution, dict): continue
        events = execution.get("events")
        if not isinstance(events, list): continue
        for event in events:
            if not isinstance(event, dict): continue
            tool = event.get("tool")
            if isinstance(tool, dict) and tool.get("name"):
                tool["name"] = readable_tool_name(tool.get("name"))
    return payload


@router.get("/v1/agent-execution-traces")
def get_agent_execution_traces(
    request: Request,
    family_key: str = "",
    execution_id: str = "",
    since: str | None = None,
    until: str | None = None,
    evidence_limit: int = 25_000,
    limit: int = 20,
    max_events_per_execution: int = 100,
    hide_disconnected: bool = False,
) -> dict[str, Any]:
    """Return privacy-safe ordered structural traces for observed agent runs."""
    _require_agent_report_read(request)
    if since and _parse(since) is None: raise HTTPException(status_code=422, detail="invalid since")
    end = _parse(until) if until else None
    if until and end is None: raise HTTPException(status_code=422, detail="invalid until")
    hidden: set[str] = set()
    try:
        raw = load_recent_evidence(
            limit=max(1, min(int(evidence_limit), 100_000)),
            since=since, agent_only=True,
        )
        if end is not None:
            raw = [row for row in raw if (_parse(row.get("observed_at")) is not None and _parse(row.get("observed_at")) < end)]
        if hide_disconnected:
            from .connections import hidden_frameworks
            switched_off = hidden_frameworks()
            # Report only frameworks whose runs were actually hidden, not every
            # connector that happens to be off or never installed.
            hidden = {fw for fw in switched_off if any(_row_framework(row) == fw for row in raw)}
            if hidden: raw = [row for row in raw if _row_framework(row) not in hidden]
        payload = agent_execution_traces(raw, family_key=family_key, execution_id=execution_id, limit=limit, max_events_per_execution=max_events_per_execution)
        payload = enrich_agent_execution_payload(payload, raw)
        payload = _sanitize_tool_names(payload)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid agent-execution-trace query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "since": since,
        "until": until,
        "hidden_frameworks": sorted(hidden),
        "evidence_is_canonical": True,
        "read_only": True,
        "writes_performed": False,
    }


@router.get("/v1/browser-ai-runs")
def get_local_browser_ai_runs(
    request: Request,
    execution_id: str = "",
    limit: int = 20,
    evidence_limit: int = 25000,
    since: str | None = None,
) -> dict[str, Any]:
    """Read-only local Web AI runs + opaque same-tab conversation references.

    The browser lifecycle must never assert a verified successful task. This
    view uses the same saved-history grant as native agent runs.
    """
    _require_agent_report_read(request)
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").strip().lower() != "ai":
        raise HTTPException(status_code=403, detail="AI-context access required")
    import os
    from shared.history_policy import active_ai_history_access
    from .db import connect
    from .local_browser_ai_runs import local_web_ai_runs

    with connect() as conn:
        return local_web_ai_runs(
            conn,
            lease=active_ai_history_access(),
            run_started_at=os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT"),
            limit=limit, evidence_limit=evidence_limit, execution_id=execution_id,
        )
