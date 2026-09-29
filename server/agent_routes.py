from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from shared.agent_evidence import AgentEvidenceError
from . import agent_telemetry_diagnostics as diagnostics
from .agent_auth import agent_bearer_matches, agent_otlp_path_token_matches
from .agent_ingest import (
    MAX_AGENT_BATCH_BYTES,
    ingest_agent_payloads,
    ingest_claude_otel_payload,
    ingest_codex_otel_payload,
    ingest_gemini_otel_payload,
    count_otlp_records,
    ingest_otel_payload,
)
from .agent_read_auth import agent_read_authorized
from .connections import FRAMEWORK_CLIENTS, is_enabled
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


def _observation_enabled_for(event: Any) -> bool:
    framework = str(event.get("framework") or "") if isinstance(event, dict) else ""
    client = FRAMEWORK_CLIENTS.get(framework)
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


def _events_channel(payload: Any) -> str:
    events = payload.get("events") if isinstance(payload, dict) else None
    first = events[0] if isinstance(events, list) and events and isinstance(events[0], dict) else {}
    return {"claude-code": "claude_code_hooks", "cursor": "cursor_hooks"}.get(str(first.get("framework") or ""), "agent_events")


async def _authorized_json(request: Request, channel: str | None) -> Any:
    """Authenticate and read the body, recording the outcome for diagnostics."""
    if channel:
        diagnostics.received(channel)
    try:
        _require_agent_write_bearer(request)
    except HTTPException:
        if channel:
            diagnostics.rejected(channel, "auth")
        raise
    try:
        return await _read_bounded_json(request)
    except HTTPException as exc:
        if channel:
            diagnostics.rejected(channel, "too_large" if exc.status_code == 413 else "invalid_payload")
        raise


@router.post(AGENT_EVENT_PATH)
async def ingest_agent_events(request: Request) -> dict[str, int | str]:
    # The native client names its channel so an auth failure is attributable
    # before the body is read; otherwise classify by the events themselves.
    hint = str(request.headers.get("x-owg-channel") or "")
    channel = hint if hint in {"claude_code_hooks", "cursor_hooks", "agent_events"} else ""
    payload = await _authorized_json(request, channel or None)
    if not channel:
        channel = _events_channel(payload)
        diagnostics.received(channel)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        diagnostics.rejected(channel, "invalid_payload")
        raise HTTPException(status_code=422, detail="events must be a list")
    events = [event for event in payload["events"] if _observation_enabled_for(event)]
    if not events:
        diagnostics.observation_off(channel)
        return {"received": 0, "status": "observation_off"}
    try:
        result = ingest_agent_payloads(events)
    except AgentEvidenceError as exc:
        diagnostics.rejected(channel, "invalid_payload")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    diagnostics.processed(channel, result)
    return {**result, "status": "ok"}


@router.post(AGENT_OTEL_PATH)
async def ingest_agent_otel(request: Request) -> dict[str, int | str]:
    channel = "otel_generic"
    payload = await _authorized_json(request, channel)
    if not isinstance(payload, dict):
        diagnostics.rejected(channel, "not_an_object")
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        result = ingest_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        diagnostics.rejected(channel, "adapter_error")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    diagnostics.processed(channel, result)
    return {**result, "status": "ok"}


@router.post(AGENT_CODEX_OTEL_PATH, status_code=202)
async def ingest_codex_otel(request: Request) -> Response:
    channel = "codex_otel"
    payload = await _authorized_json(request, channel)
    if not isinstance(payload, dict):
        diagnostics.rejected(channel, "not_an_object")
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    if not is_enabled("codex", "observe"):
        diagnostics.observation_off(channel)
        return Response(status_code=202)
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        result = ingest_codex_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        diagnostics.rejected(channel, "adapter_error")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    diagnostics.processed(channel, result)
    return Response(status_code=202)


@router.post(AGENT_CLAUDE_OTEL_PATH, status_code=202)
async def ingest_claude_otel(request: Request) -> Response:
    channel = "claude_code_otel_logs"
    payload = await _authorized_json(request, channel)
    if not isinstance(payload, dict):
        diagnostics.rejected(channel, "not_an_object")
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    if not is_enabled("claude_code", "observe"):
        diagnostics.observation_off(channel)
        return Response(status_code=202)
    defaults_raw = payload.pop("openworkgraph", {})
    try:
        defaults = OTelDefaults.model_validate(defaults_raw if isinstance(defaults_raw, dict) else {}).model_dump()
        result = ingest_claude_otel_payload(payload, defaults=defaults)
    except (AgentEvidenceError, ValueError) as exc:
        diagnostics.rejected(channel, "adapter_error")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    diagnostics.processed(channel, result)
    return Response(status_code=202)


# Clients that can be pointed at an OTLP endpoint from their settings file but
# cannot send an Authorization header from there. The path carries a separate
# write-only token instead (server.agent_auth.ensure_agent_otlp_path_token).
OTLP_SOURCES: dict[str, dict[str, str]] = {
    "copilot": {"client": "vscode", "framework": "github-copilot", "agent_name": "GitHub Copilot",
                "provider": "github", "channel": "copilot_otel"},
    "gemini": {"client": "gemini_cli", "framework": "gemini-cli", "agent_name": "Gemini CLI",
               "provider": "google", "channel": "gemini_otel"},
}
MAX_OTLP_DECOMPRESSED_BYTES = MAX_AGENT_BATCH_BYTES


async def _read_otlp_json(request: Request, channel: str) -> Any:
    content_type = str(request.headers.get("content-type") or "").lower()
    if "protobuf" in content_type:
        diagnostics.rejected(channel, "protobuf_unsupported")
        raise HTTPException(status_code=415, detail="OpenWorkGraph accepts OTLP/HTTP JSON only; use the http/json protocol")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_AGENT_BATCH_BYTES:
            diagnostics.rejected(channel, "too_large")
            raise HTTPException(status_code=413, detail=f"agent request exceeds {MAX_AGENT_BATCH_BYTES} bytes")
    raw = bytes(body)
    if "gzip" in str(request.headers.get("content-encoding") or "").lower():
        import zlib

        try:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            raw = inflater.decompress(raw, MAX_OTLP_DECOMPRESSED_BYTES + 1)
        except zlib.error as exc:
            diagnostics.rejected(channel, "invalid_payload")
            raise HTTPException(status_code=400, detail="invalid gzip body") from exc
        if len(raw) > MAX_OTLP_DECOMPRESSED_BYTES or inflater.unconsumed_tail:
            diagnostics.rejected(channel, "too_large")
            raise HTTPException(status_code=413, detail="decompressed agent request is too large")
        # A decodable prefix is not a valid gzip request. Reject truncated streams
        # and concatenated/trailing members rather than accepting partial JSON.
        if not inflater.eof or inflater.unused_data:
            diagnostics.rejected(channel, "invalid_payload")
            raise HTTPException(status_code=400, detail="invalid gzip body")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        diagnostics.rejected(channel, "invalid_payload")
        raise HTTPException(status_code=400, detail="invalid OTLP JSON payload") from exc
    if not isinstance(payload, dict):
        diagnostics.rejected(channel, "not_an_object")
        raise HTTPException(status_code=422, detail="OpenTelemetry payload must be an object")
    return payload


@router.post("/agent-ingest/otlp/{source}/{token}/v1/{signal}")
async def ingest_path_token_otlp(source: str, token: str, signal: str, request: Request) -> dict[str, Any]:
    spec = OTLP_SOURCES.get(source)
    if spec is None or signal not in {"traces", "logs", "metrics"}:
        raise HTTPException(status_code=404, detail="unknown OTLP source")
    channel = spec["channel"]
    diagnostics.received(channel)
    if not agent_otlp_path_token_matches(token):
        diagnostics.rejected(channel, "auth")
        raise HTTPException(status_code=401, detail="agent ingest authentication required")
    payload = await _read_otlp_json(request, channel)
    if not is_enabled(spec["client"], "observe"):
        diagnostics.observation_off(channel)
        return {}
    defaults = {"framework": spec["framework"], "agent_name": spec["agent_name"], "provider": spec["provider"]}
    try:
        if signal == "traces":
            result = ingest_otel_payload(payload, defaults=defaults)
        elif signal == "logs" and source == "gemini":
            result = ingest_gemini_otel_payload(payload, defaults=defaults)
        else:
            # Metrics, and Copilot's logs (which repeat its spans), are accepted so
            # the exporter stays healthy, and counted, but nothing is stored.
            count = count_otlp_records(payload)
            result = {"records_seen": count, "records_ignored": count, "projected": 0, "inserted": 0}
    except (AgentEvidenceError, ValueError) as exc:
        diagnostics.rejected(channel, "adapter_error")
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    diagnostics.processed(channel, result)
    return {}  # the OTLP/HTTP JSON success response


@router.get("/v1/agent-telemetry/diagnostics")
def get_agent_telemetry_diagnostics(request: Request) -> dict[str, Any]:
    """Per-channel delivery counts so a missing signal can be located, not guessed."""
    _require_api_read_bearer(request)
    from .agent_capture_runtime import configuration_state
    from .setup_checks import checks

    snapshot, configuration = diagnostics.snapshot(), configuration_state()
    extras: dict[str, Any] = {}
    try:
        from . import agent_brief, outcome_tracker

        extras = {"outcome_tracking": outcome_tracker.status(), "agent_brief": agent_brief.status()}
    except Exception:
        pass
    return {**snapshot, "configuration": configuration, "checks": checks(snapshot, configuration, extras=extras)}


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
