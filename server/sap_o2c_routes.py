"""Dashboard routes for the SAP order-to-cash source; included from local_source_routes."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from . import sap_o2c_sync as sync
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "sap_o2c.js"


@router.get("/sap-o2c.js")
def sap_o2c_script() -> FileResponse:
    return FileResponse(_SCRIPT, media_type="application/javascript")


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required for the SAP source")


def _call(operation, *args: Any, **kwargs: Any) -> JSONResponse:
    try:
        return JSONResponse(operation(*args, **kwargs), headers={"Cache-Control": "no-store"})
    except sync.SyncError as exc:
        raise HTTPException(exc.status, {"code": exc.code, "message": str(exc)}) from None


@router.get("/v1/sources/sap-o2c")
async def sap_o2c_status(request: Request):
    _require_dashboard(request)
    return _call(sync.status)


@router.put("/v1/sources/sap-o2c")
async def sap_o2c_configure(request: Request):
    _require_dashboard(request)
    body = await request.body()
    if len(body) > 4096:
        raise HTTPException(413, "configuration is too large")
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(422, "configuration must be JSON") from None
    return _call(sync.configure, payload)


@router.post("/v1/sources/sap-o2c/run")
async def sap_o2c_run(request: Request):
    _require_dashboard(request)
    if request.headers.get("x-owg-source-read-confirmed") != "true":
        raise HTTPException(403, "Confirm this read from SAP and the local event-log write first")
    return await run_in_threadpool(_call, sync.run)


@router.post("/v1/sources/sap-o2c/disconnect")
async def sap_o2c_disconnect(request: Request):
    _require_dashboard(request)
    return _call(sync.disconnect)
