from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from shared.agent_evidence import AgentEvidenceError
from .agent_auth import agent_bearer_matches
from .agent_ingest import ingest_agent_payloads, ingest_otel_payload
from .agent_workflows import agent_workflow_view
from .local_auth import bearer_matches

router = APIRouter()


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


@router.post("/v1/agent-events")
def ingest_agent_events(batch: AgentEventBatch, request: Request) -> dict[str, int | str]:
    _require_agent_write_bearer(request)
    try:
        result = ingest_agent_payloads(batch.events)
    except AgentEvidenceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "status": "ok"}


@router.post("/v1/agent-events/otel")
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
