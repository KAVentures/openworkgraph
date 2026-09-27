from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from shared.agent_evidence import AgentEvidenceError
from .agent_auth import agent_bearer_matches
from .agent_ingest import (
    MAX_AGENT_BATCH_BYTES,
    ingest_agent_payloads,
    ingest_claude_otel_payload,
    ingest_codex_otel_payload,
    ingest_otel_payload,
)
from .agent_read_auth import agent_read_authorized
from .connections import is_enabled
from .agent_workflows import agent_workflow_view
from .agent_execution_trace_routes import router as agent_execution_trace_router
from .context_execution_routes import router as context_execution_router
from .context_outcome_routes import router as context_outcome_router
from .declared_policy_routes import router as declared_policy_router
from .policy_action_routes import router as policy_action_router
from .policy_guard_routes import router as policy_guard_router
from .procedural_memory_routes import router as procedural_memory_router
from .shadow_enforcement_routes import router as shadow_enforcement_router
from .task_context_routes import router as task_context_router

router = APIRouter()

AGENT_EVENT_PATH = "/agent-ingest/v1/events"
AGENT_OTEL_PATH = "/agent-ingest/v1/otel"
AGENT_CODEX_OTEL_PATH = "/agent-ingest/v1/codex-otel"
AGENT_CLAUDE_OTEL_PATH = "/agent-ingest/v1/claude-otel"


class OTelDefaults(BaseModel):
    organization_id: str = ""
    actor_id: str = ""
    device_id: str = ""
    agent_name: str = ""
    provider: str = ""
    framework: str = ""
    run_id: str = ""
    workflow_id: str = ""


_SWITCHABLE_FRAMEWORKS = {"claude-code": "claude_code", "codex": "codex"}


def _observation_enabled_for(event: Any) -> bool:
    framework = str(event.get("framework") or "") if isinstance(event, dict) else ""
    client = _SWITCHABLE_FRAMEWORKS.get(framework)
    return is_enabled(client, "observe") if client else True


def _require_agent_write_bearer(request: Request) -> None:
    if not agent_bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="agent ingest authentication required")


def _require_api_read_bearer(request: Request) -> None:
    if not agent_read_authorized(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API or dashboard authentication required")


async def _read_bounded_json(request: Request) -> Any:
    content_length = str(request.headers.get("content-length") or "").strip()
    if content_length:
        try:
            declared = int(content_length)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid Content-Length") from exc
        if declared < 0:
            raise HTTPException(status_code=400, detail="invalid Content-Length")
        if declared > MAX_AGENT_BATCH_BYTES:
            raise HTTPException(status_code=413, detail=f"agent request exceeds {MAX_AGENT_BATCH_BYTES} bytes")

    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_AGENT_BATCH_BYTES:
            raise HTTPException(status_code=413, detail=f"agent request exceeds {MAX_AGENT_BATCH_BYTES} bytes")
    try:
        return json.loads(bytes(body).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid agent JSON payload") from exc


@router.post(AGENT_EVENT_PATH)
async def ingest_agent_events(request: Request) -> dict[str, int | str]:
    _require_agent_write_bearer(request)
    payload = await _read_bounded_json(request)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise HTTPException(status_code=422, detail="events must be a list")
    events = [event for event in payload["events"] if _observation_enabled_for(event)]
    if not events:
        return {"received": 0, "status": "observation_off"}
    try:
        result = ingest_agent_payloads(events)
    except AgentEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": "ok"}


@router.post(AGENT_OTEL_PATH)
async def ingest_agent_otel(request: Request) -> dict[str, int | str]:
    _require_agent_write_bearer(request)
    payload = await _read_bounded_json(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        result = ingest_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": "ok"}


@router.post(AGENT_CODEX_OTEL_PATH, status_code=202)
async def ingest_codex_otel(request: Request) -> Response:
    _require_agent_write_bearer(request)
    payload = await _read_bounded_json(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    if not is_enabled("codex", "observe"):
        return Response(status_code=202)
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        ingest_codex_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=202)


@router.post(AGENT_CLAUDE_OTEL_PATH, status_code=202)
async def ingest_claude_otel(request: Request) -> Response:
    _require_agent_write_bearer(request)
    payload = await _read_bounded_json(request)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    if not is_enabled("claude_code", "observe"):
        return Response(status_code=202)
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        ingest_claude_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(status_code=202)


@router.get("/v1/agent-workflows")
def get_agent_workflows(request: Request, since: str | None = None, limit: int = 5000) -> dict[str, Any]:
    _require_api_read_bearer(request)
    return agent_workflow_view(limit=limit, since=since)


router.include_router(procedural_memory_router)
router.include_router(declared_policy_router)
router.include_router(task_context_router)
router.include_router(context_execution_router)
router.include_router(agent_execution_trace_router)
router.include_router(context_outcome_router)
router.include_router(shadow_enforcement_router)
router.include_router(policy_action_router)
router.include_router(policy_guard_router)
