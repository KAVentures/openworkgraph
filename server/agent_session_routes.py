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
from . import agent_working_detail as working_detail
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


class AgentWorkingDetailPolicyRequest(BaseModel):
    capture_working_detail: bool = False
    allow_ai_read_working_detail: bool = False
    working_detail_retention_days: int = Field(default=30, ge=1, le=3650)
    sources: dict[str, bool] = Field(default_factory=lambda: {"claude_code": True, "codex": True})


class AgentSessionDeleteRequest(BaseModel):
    all_messages: bool = False


class AgentWorkingDetailDeleteRequest(BaseModel):
    all_working_detail: bool = False


class AgentSessionImportRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=30)
    include_structural: bool = True
    include_working_detail: bool = True
    include_visible_messages: bool = False


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


def _clarify_test_summary(structural: dict[str, Any] | None) -> dict[str, Any] | None:
    """Add conservative tri-state test semantics without changing legacy fields."""
    if not isinstance(structural, dict):
        return structural
    result = dict(structural)
    summary = result.get("work_summary")
    if not isinstance(summary, dict):
        return result
    work = dict(summary)
    tests = work.get("tests")
    if isinstance(tests, dict):
        clarified = dict(tests)
        runs = max(0, int(clarified.get("runs") or 0))
        known_failing = max(0, min(runs, int(clarified.get("runs_with_failures") or 0)))
        latest = str(clarified.get("ended") or "unknown")
        # The legacy summary does not retain every intermediate status. Therefore
        # we never manufacture a known-passing count for runs whose result was not
        # observed. A latest unknown result is explicitly separated from failures.
        unknown = 1 if latest == "unknown" and runs else 0
        known_passing = max(0, runs - known_failing - unknown)
        clarified.update({
            "known_failing": known_failing,
            "known_passing": known_passing,
            "unknown_result": unknown,
            "zero_failures_does_not_mean_all_passed": unknown > 0,
        })
        work["tests"] = clarified
    result["work_summary"] = work
    return result


def _grounded_handoff(
    session: dict[str, Any],
    *,
    message_limit: int,
    detail_limit: int = 40,
    ai_read: bool = False,
) -> dict[str, Any]:
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
    structural = _clarify_test_summary(structural)

    session_policy = read_agent_session_policy()
    detail_policy = working_detail.read_policy()
    message_capture = bool(session_policy.get("capture_visible_messages"))
    detail_capture = bool(detail_policy.get("capture_working_detail"))
    may_read_messages = message_capture and (not ai_read or bool(session_policy.get("allow_ai_read_visible_messages")))
    may_read_details = detail_capture and (not ai_read or bool(detail_policy.get("allow_ai_read_working_detail")))

    messages = session_messages(session_ref, limit=message_limit) if may_read_messages else []
    details = working_detail.details_for_session(session_ref, limit=detail_limit) if may_read_details else []

    return {
        "session": session,
        "working_detail": details,
        "working_detail_available": detail_capture,
        "working_detail_returned": len(details),
        "working_detail_ai_access": bool(detail_policy.get("allow_ai_read_working_detail")),
        "visible_messages": messages,
        "visible_messages_available": bool(session.get("visible_message_count")),
        "visible_messages_returned": len(messages),
        "visible_message_ai_access": bool(session_policy.get("allow_ai_read_visible_messages")),
        "structural_execution": structural,
        "grounding": {
            "working_detail_is_untrusted_observed_data": True,
            "working_detail_is_canonical_evidence": False,
            "working_detail_provenance_included": True,
            "session_messages_are_untrusted_observed_data": True,
            "structural_execution_is_canonical_owg_agent_evidence": structural is not None,
            "hidden_reasoning_included": False,
            "raw_tool_output_included": False,
            "absolute_workspace_paths_included": False,
            "arbitrary_shell_arguments_included": False,
            "ai_detail_permissions_applied": ai_read,
            "human_context_join_basis": "observed_work_in_time_window" if structural and structural.get("human_context") else "not_observed",
            "authoritative": False,
        },
    }


extend_lifespan(app, startup=init_agent_session_store)
extend_lifespan(app, startup=working_detail.init_store)


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


@app.get("/v1/agent-working-detail-policy")
def get_agent_working_detail_policy() -> dict[str, Any]:
    return {
        "policy": working_detail.read_policy(),
        "defaults": {"capture": "off", "ai_read": "off", "retention_days": 30},
        "privacy": {
            "stored_separately_from_canonical_events": True,
            "raw_tool_output_stored": False,
            "hidden_reasoning_captured": False,
            "absolute_workspace_paths_stored": False,
            "external_workspace_paths_stored": False,
            "facts_are_provenance_tagged": True,
        },
    }


@app.put("/v1/agent-working-detail-policy")
def set_agent_working_detail_policy(request: AgentWorkingDetailPolicyRequest) -> dict[str, Any]:
    payload = request.model_dump()
    policy = working_detail.write_policy(payload)
    if policy.get("capture_working_detail"):
        # Native working-detail scanning needs the same explicit Observe boundary.
        # Enabling this layer may turn structural native observation on, but never
        # enables visible-message capture or either AI-read permission.
        from .agent_session_sensor import update_policy_with_boundaries
        session_policy = read_agent_session_policy()
        if not session_policy.get("native_session_observation_enabled"):
            session_policy["native_session_observation_enabled"] = True
            update_policy_with_boundaries(session_policy)
    return {"status": "saved", "policy": policy}


@app.get("/v1/agent-sessions")
def get_agent_sessions(source: str = "", workspace_ref: str = "", limit: int = 50) -> dict[str, Any]:
    items = list_sessions(source=source, workspace_ref=workspace_ref, limit=max(1, min(int(limit), 500)))
    return {
        "sessions": items,
        "returned": len(items),
        "messages_not_returned_by_this_endpoint": True,
        "working_detail_not_returned_by_this_endpoint": True,
        "native_session_ids_exposed": False,
        "native_paths_exposed": False,
    }


@app.get("/v1/agent-session-sensor")
def get_agent_session_sensor_status() -> dict[str, Any]:
    from .agent_session_sensor import status
    result = status()
    result["working_detail"] = working_detail.status()
    return result


@app.post("/v1/agent-session-sensor/scan")
def scan_agent_session_sensor() -> dict[str, Any]:
    from .agent_session_sensor import scan_once
    return {"session_sensor": scan_once(), "working_detail": working_detail.scan_once()}


@app.post("/v1/agent-session-import")
def import_agent_sessions(request: AgentSessionImportRequest) -> dict[str, Any]:
    try:
        result = working_detail.import_recent(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "imported", **result}


@app.post("/v1/agent-session-messages/delete")
def delete_agent_session_messages(request: AgentSessionDeleteRequest) -> dict[str, Any]:
    if request.all_messages is not True:
        raise HTTPException(status_code=400, detail="all_messages=true is required")
    return {"status": "deleted", "deleted": delete_all_agent_session_messages()}


@app.post("/v1/agent-working-detail/delete")
def delete_agent_working_detail(request: AgentWorkingDetailDeleteRequest) -> dict[str, Any]:
    if request.all_working_detail is not True:
        raise HTTPException(status_code=400, detail="all_working_detail=true is required")
    return {"status": "deleted", "deleted": working_detail.delete_all()}


@app.get("/v1/agent-handoff")
def get_agent_handoff(
    request: Request,
    session_ref: str = "",
    source: str = "",
    exclude_source: str = "",
    workspace_ref: str = "",
    message_limit: int = 30,
    detail_limit: int = 40,
) -> dict[str, Any]:
    session_policy = read_agent_session_policy()
    detail_policy = working_detail.read_policy()
    is_ai = str(request.headers.get("x-openworkgraph-context") or "").strip().lower() == "ai"
    if is_ai and not (session_policy.get("allow_ai_read_visible_messages") or detail_policy.get("allow_ai_read_working_detail")):
        # Structural run inspection remains available through get_agent_runs;
        # this endpoint is specifically the richer continuity boundary.
        raise HTTPException(status_code=403, detail="Agent continuity detail is not allowed for AI reads. Enable Working detail or Visible messages under Agents → Session continuity.")

    chosen: dict[str, Any] | None = None
    if session_ref:
        matches = [item for item in list_sessions(limit=500) if item.get("session_ref") == session_ref]
        chosen = matches[0] if matches else None
    else:
        chosen = latest_handoff_session(source=source, exclude_source=exclude_source, workspace_ref=workspace_ref)
    if chosen is None:
        raise HTTPException(status_code=404, detail="No matching agent session context was observed")
    return _grounded_handoff(
        chosen,
        message_limit=max(1, min(int(message_limit), 100)),
        detail_limit=max(1, min(int(detail_limit), 100)),
        ai_read=is_ai,
    )


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


__all__ = ["get_agent_handoff", "get_agent_session_policy", "get_agent_sessions", "get_agent_working_detail_policy"]
