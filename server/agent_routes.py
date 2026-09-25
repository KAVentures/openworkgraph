from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from shared.agent_evidence import AgentEvidenceError
from .agent_auth import agent_bearer_matches
from .agent_ingest import ingest_agent_payloads, ingest_codex_otel_payload, ingest_otel_payload
from .agent_workflows import agent_workflow_view
from .context_execution_routes import router as context_execution_router
from .context_outcome_routes import router as context_outcome_router
from .declared_policy_routes import router as declared_policy_router
from .local_auth import bearer_matches
from .policy_action_routes import router as policy_action_router
from .procedural_memory_routes import router as procedural_memory_router
from .task_context_routes import router as task_context_router

router = APIRouter()

# Agent write ingress deliberately sits outside /v1/. The secure local server's
# /v1/* namespace accepts the broader API/dashboard credential, while these
# exact write endpoints authenticate only the least-privilege agent token.
AGENT_EVENT_PATH = "/agent-ingest/v1/events"
AGENT_OTEL_PATH = "/agent-ingest/v1/otel"
AGENT_CODEX_OTEL_PATH = "/agent-ingest/v1/codex-otel"


class AgentEventBatch(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


class OTelDefaults(BaseModel):
    organization_id: str = ""
    actor_id: str = ""
    device_id: str = ""
    agent_name: str = ""
    provider: str = ""
    framework: str = ""
    run_id: str = ""
    workflow_id: str = ""


def _require_agent_write_bearer(request: Request) -> None:
    # Agent runtimes receive only this write credential. It is deliberately not
    # the broader API bearer used to read/export work history.
    if not agent_bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="agent ingest authentication required")


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.post(AGENT_EVENT_PATH)
def ingest_agent_events(batch: AgentEventBatch, request: Request) -> dict[str, int | str]:
    _require_agent_write_bearer(request)
    try:
        result = ingest_agent_payloads(batch.events)
    except AgentEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": "ok"}


@router.post(AGENT_OTEL_PATH)
async def ingest_agent_otel(request: Request) -> dict[str, int | str]:
    _require_agent_write_bearer(request)
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid OpenTelemetry JSON payload") from exc
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
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid Codex OpenTelemetry JSON payload") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Codex OpenTelemetry payload must be an object")

    # Codex sends standard OTLP JSON. Optional OpenWorkGraph defaults are useful
    # for custom relays/tests, but normal Codex exporters need no OWG-specific body.
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        ingest_codex_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # OTLP clients only need a successful HTTP status. Codex's own exporter tests
    # accept an empty 202 response, avoiding dependence on non-standard OWG JSON.
    return Response(status_code=202)


@router.get("/v1/agent-workflows")
def get_agent_workflows(
    request: Request,
    since: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    # Workflow views can include human trigger surfaces. Do not grant this read
    # to the write-only agent token.
    _require_api_read_bearer(request)
    return agent_workflow_view(limit=limit, since=since)


# Procedural memory, declared policy, unified task context, context/execution
# linkage, aggregate context/outcome associations, and policy action advisory are
# read-only derived/governance views. They inherit secure_app composition through
# this existing additive router and never receive the write-only agent credential.
router.include_router(procedural_memory_router)
router.include_router(declared_policy_router)
router.include_router(task_context_router)
router.include_router(context_execution_router)
router.include_router(context_outcome_router)
router.include_router(policy_action_router)
