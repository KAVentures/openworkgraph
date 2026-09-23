from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from connector.control import set_sharing, status as gateway_status
from connector.runtime import restart_sync_worker, start_sync_worker, status as worker_status, stop_sync_worker
from connector.service import disconnect_endpoint, enroll_endpoint
from shared.lifespan import extend_lifespan
from .main import CONFIG_PATH, ROOT
from .secure_app import app


class GatewayEnrollmentRequest(BaseModel):
    gateway_url: str
    organization_id: str
    actor_id: str = ""
    enrollment_token: str
    verify_tls: bool = True
    allow_insecure_http: bool = False


class GatewaySharingRequest(BaseModel):
    enabled: bool


class GatewayDisconnectRequest(BaseModel):
    force_local: bool = False


def _demo_mode() -> bool:
    return os.getenv("WORKFLOW_OBSERVER_MODE", "observe") == "demo"


def _combined_status() -> dict[str, Any]:
    value = gateway_status(CONFIG_PATH)
    value.update(worker_status())
    value["local_capture_continues_when_paused"] = True
    value["cloud_account_required"] = False
    value["raw_rich_evidence_canonical"] = True
    if _demo_mode():
        value["mode"] = "demo_local_only"
        value["worker_running"] = False
    return value


def _start_optional_gateway_worker() -> None:
    if not _demo_mode():
        start_sync_worker(CONFIG_PATH)


def _stop_optional_gateway_worker() -> None:
    stop_sync_worker()


extend_lifespan(
    app,
    startup=_start_optional_gateway_worker,
    shutdown=_stop_optional_gateway_worker,
)


@app.get("/v1/gateway-status")
def get_gateway_status() -> dict[str, Any]:
    return _combined_status()


@app.post("/v1/gateway-enroll")
def enroll_gateway(request: GatewayEnrollmentRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway enrollment is disabled in demo mode")
    try:
        result = enroll_endpoint(
            CONFIG_PATH,
            gateway_url=request.gateway_url,
            organization_id=request.organization_id,
            actor_id=request.actor_id,
            enrollment_token=request.enrollment_token,
            verify_tls=request.verify_tls,
            allow_insecure_http=request.allow_insecure_http,
        )
        restart_sync_worker(CONFIG_PATH)
        return {**result, **_combined_status()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Gateway enrollment failed: {str(exc)[:300]}") from exc


@app.post("/v1/gateway-sharing")
def update_gateway_sharing(request: GatewaySharingRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway sharing is disabled in demo mode")
    try:
        result = set_sharing(CONFIG_PATH, request.enabled)
        if request.enabled:
            start_sync_worker(CONFIG_PATH)
        return {**result, **worker_status()}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:300]) from exc


@app.post("/v1/gateway-disconnect")
def disconnect_gateway(request: GatewayDisconnectRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Gateway controls are disabled in demo mode")
    try:
        stop_sync_worker()
        result = disconnect_endpoint(CONFIG_PATH, require_remote_revoke=not request.force_local)
        return {**result, **worker_status()}
    except Exception as exc:
        start_sync_worker(CONFIG_PATH)
        raise HTTPException(
            status_code=502,
            detail=(
                "Could not revoke the device credential at the Gateway. Sharing remains configured. "
                "Pause sharing instead, or retry when the Gateway is reachable. "
                f"Details: {str(exc)[:220]}"
            ),
        ) from exc


@app.get("/gateway-panel.js")
def gateway_panel_script() -> Response:
    path = ROOT / "dashboard" / "gateway_panel.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


@app.middleware("http")
async def inject_gateway_panel(request: Request, call_next):
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
    marker = '<script src="/gateway-panel.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


__all__ = ["app"]
