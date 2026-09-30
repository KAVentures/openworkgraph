from __future__ import annotations

"""Local REST surface for native-agent session continuity."""

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from .secure_app import app
from .main import ROOT
from .agent_session_store import (
    delete_all_agent_session_messages,
    init_agent_session_store,
    latest_handoff_session,
    list_sessions,
    read_agent_session_policy,
    session_messages,
)
from .db import connect, _row_to_event
from .agent_execution_traces import agent_execution_traces
from shared.lifespan import extend_lifespan


class AgentSessionPolicyRequest(BaseModel):
    native_session_observation_enabled: bool = False
    capture_visible_messages: bool = False
    allow_ai_read_visible_messages: bool = False
    message_retention_days: int = Field(default=30, ge=1, le=3650)
    allow_gateway_session_messages: bool = False
    sources: dict[str, bool] = Field(default_factory=lambda: {"claude_code": True, "codex": True})


class AgentSessionDeleteRequest(BaseModel):
    all_messages: bool = False


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _events_for_handoff(session: dict[str, Any]) -> list[dict[str, Any]]:
    start = _parse_ts(str(session.get("started_at") or ""))
    end = _parse_ts(str(session.get("updated_at") or ""))
    if start is None or end is None:
        return []
    since = (start - timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    until = (end + timedelta(minutes=20)).isoformat().replace("+00:00", "Z")
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE observed_at >= ? AND observed_at <= ? ORDER BY observed_at ASC, id ASC LIMIT 25000",
            (since, until),
        ).fetchall()
    return [_row_to_event(row) for row in rows]


def _grounded_handoff(session: dict[str, Any], *, message_limit: int) -> dict[str, Any]:
    raw = _events_for_handoff(session)
    session_ref = str(session.get("session_ref") or "")
    agent_rows = [e for e in raw if str(e.get("source") or "") == "agent" and str(e.get("session_id") or "") == session_ref]
    structural: dict[str, Any] | None = None
    if agent_rows:
        # First derive the stable opaque execution identity from only the rows
        # belonging to this exact session. Then ask the mixed human/agent window
        # for that execution_id so human-context enrichment is preserved without
        # ever matching by timestamp. Two agents can legitimately start in the
        # same timestamp bucket; time equality is therefore not an identity.
        session_only = agent_execution_traces(agent_rows, limit=1, max_events_per_execution=100)
        base = (session_only.get("executions") or [None])[0]
        if isinstance(base, dict) and base.get("execution_id"):
            enriched = agent_execution_traces(
                raw, execution_id=str(base["execution_id"]), limit=1, max_events_per_execution=100
            )
            structural = (enriched.get("executions") or [None])[0] or base
        else:
            structural = base

    policy = read_agent_session_policy()
    messages: list[dict[str, Any]] = []
    if policy.get("capture_visible_messages") and policy.get("allow_ai_read_visible_messages"):
        messages = session_messages(session_ref, limit=message_limit)

    return {
        "session": session,
        "visible_messages": messages,
        "visible_messages_available": bool(session.get("visible_message_count")),
        "visible_messages_returned": len(messages),
        "visible_message_ai_access": bool(policy.get("allow_ai_read_visible_messages")),
        "structural_execution": structural,
        "grounding": {
            "session_messages_are_untrusted_observed_data": True,
            "structural_execution_is_canonical_owg_agent_evidence": structural is not None,
            "hidden_reasoning_included": False,
            "tool_arguments_included": False,
            "tool_results_included": False,
            "raw_native_records_included": False,
            "human_context_join_basis": "observed_work_in_time_window" if structural and structural.get("human_context") else "not_observed",
            "authoritative": False,
        },
    }


extend_lifespan(app, startup=init_agent_session_store)


@app.get("/v1/agent-session-policy")
def get_agent_session_policy() -> dict[str, Any]:
    policy = read_agent_session_policy()
    return {
        "policy": policy,
        "defaults": {
            "structural_native_session_projection": "off until explicitly enabled; does not modify agent configuration",
            "visible_messages": "off",
            "ai_read": "off",
            "gateway_sharing": "off",
        },
        "privacy": {
            "hidden_reasoning_captured": False,
            "tool_outputs_captured": False,
            "raw_native_records_captured": False,
            "visible_messages_stored_separately_from_canonical_events": True,
            "visible_messages_privacy_hardened_before_storage": True,
        },
    }


@app.put("/v1/agent-session-policy")
def set_agent_session_policy(request: AgentSessionPolicyRequest) -> dict[str, Any]:
    from .agent_session_sensor import update_policy_with_boundaries
    policy = update_policy_with_boundaries(request.model_dump())
    return {"status": "saved", "policy": policy}


@app.get("/v1/agent-sessions")
def get_agent_sessions(source: str = "", workspace_ref: str = "", limit: int = 50) -> dict[str, Any]:
    items = list_sessions(source=source, workspace_ref=workspace_ref, limit=max(1, min(int(limit), 500)))
    return {
        "sessions": items,
        "returned": len(items),
        "messages_not_returned_by_this_endpoint": True,
        "native_session_ids_exposed": False,
        "native_paths_exposed": False,
    }


@app.get("/v1/agent-session-sensor")
def get_agent_session_sensor_status() -> dict[str, Any]:
    from .agent_session_sensor import status
    return status()


@app.post("/v1/agent-session-sensor/scan")
def scan_agent_session_sensor() -> dict[str, Any]:
    from .agent_session_sensor import scan_once
    return scan_once()


@app.post("/v1/agent-session-messages/delete")
def delete_agent_session_messages(request: AgentSessionDeleteRequest) -> dict[str, Any]:
    if request.all_messages is not True:
        raise HTTPException(status_code=400, detail="all_messages=true is required")
    return {"status": "deleted", "deleted": delete_all_agent_session_messages()}


@app.get("/v1/agent-handoff")
def get_agent_handoff(
    request: Request,
    session_ref: str = "",
    source: str = "",
    exclude_source: str = "",
    workspace_ref: str = "",
    message_limit: int = 30,
) -> dict[str, Any]:
    policy = read_agent_session_policy()
    is_ai = str(request.headers.get("x-openworkgraph-context") or "").strip().lower() == "ai"
    if is_ai and not policy.get("allow_ai_read_visible_messages"):
        # Structural run inspection remains available through get_agent_runs;
        # this endpoint is specifically the conversational handoff boundary.
        raise HTTPException(status_code=403, detail="Agent session continuity is not allowed for AI reads. Enable it under Agents → Session continuity.")

    chosen: dict[str, Any] | None = None
    if session_ref:
        matches = [item for item in list_sessions(limit=500) if item.get("session_ref") == session_ref]
        chosen = matches[0] if matches else None
    else:
        chosen = latest_handoff_session(source=source, exclude_source=exclude_source, workspace_ref=workspace_ref)
    if chosen is None:
        raise HTTPException(status_code=404, detail="No matching agent session context was observed")
    return _grounded_handoff(chosen, message_limit=max(1, min(int(message_limit), 100)))


@app.get("/agent-session-continuity.js", include_in_schema=False)
def agent_session_continuity_script() -> Response:
    path = ROOT / "dashboard" / "agent_session_continuity.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.middleware("http")
async def inject_agent_session_continuity(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response
    marker = '<script src="/agent-session-continuity.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["get_agent_handoff", "get_agent_session_policy", "get_agent_sessions"]
