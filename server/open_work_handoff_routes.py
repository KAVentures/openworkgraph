from __future__ import annotations

"""REST surface for owner-offered Open Work hand-off (see open_work_dispatch)."""
import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from . import open_work_dispatch as dispatch
from .local_auth import bearer_matches, dashboard_session_valid

router = APIRouter()
_MAX_BODY = 2048


def _require_dashboard(request: Request) -> None:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session ") or not dashboard_session_valid(raw[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="explicit local dashboard session required")


def _require_agent(request: Request) -> None:
    # Do not rely solely on the enclosing app middleware: a router mount must
    # never turn an owner-offered case into an unauthenticated agent capability.
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="local agent bearer required")


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="JSON dispatch request required")
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > _MAX_BODY:
                raise HTTPException(status_code=413, detail="dispatch request too large")
            raw.extend(chunk)

        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON field")
                result[key] = value
            return result

        def reject_constant(_value):
            raise ValueError("nonstandard JSON number")

        try:
            # Preserve the existing empty-body contract; semantic validation stays in each route.
            data = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=reject_constant) if raw else {}
        except (ValueError, UnicodeError, RecursionError):
            raise HTTPException(status_code=422, detail="invalid JSON") from None
    finally:
        raw.clear()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="expected an object")
    return data

async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except dispatch.DispatchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail="dispatch queue could not be saved") from None


# Owner (dashboard) actions.
@router.get("/v1/open-work/dispatch")
async def dispatch_status(request: Request):
    _require_dashboard(request)
    return await _run(dispatch.status, owner=True)


@router.post("/v1/open-work/dispatch/settings")
async def dispatch_settings(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return await _run(dispatch.set_enabled, data.get("enabled"), lease_minutes=data.get("lease_minutes"))


@router.post("/v1/open-work/dispatch/offers")
async def dispatch_offer(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    if data.get("confirm_offer") is not True:
        raise HTTPException(status_code=403, detail="explicit confirmation required to offer a case to agents")
    return await _run(dispatch.offer, data.get("work_item_ref"), owner_note=data.get("owner_note", ""),
                      days=data.get("days", 3))


@router.post("/v1/open-work/dispatch/offers/withdraw")
async def dispatch_withdraw(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return {"removed": await _run(dispatch.withdraw, data.get("work_item_ref"))}


@router.post("/v1/open-work/dispatch/offers/acknowledge")
async def dispatch_acknowledge(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    if type(data.get("reoffer")) is not bool:
        raise HTTPException(status_code=422, detail="reoffer must be true or false")
    return {"offer": await _run(dispatch.acknowledge, data.get("work_item_ref"), reoffer=data["reoffer"])}


# Agent actions (called by the local MCP tools with the local API token).
@router.get("/v1/open-work/dispatch/agent")
async def agent_offers(request: Request):
    _require_agent(request)
    result = await _run(dispatch.status, owner=False)
    result["offers"] = [o for o in result["offers"] if o["status"] == "offered"] if result["enabled"] else []
    return result


@router.post("/v1/open-work/dispatch/agent/claim")
async def agent_claim(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.claim, data.get("work_item_ref"), data.get("agent"))


@router.post("/v1/open-work/dispatch/agent/release")
async def agent_release(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.release, data.get("work_item_ref"), data.get("lease_token"))


@router.post("/v1/open-work/dispatch/agent/report")
async def agent_report(request: Request):
    _require_agent(request)
    data = await _body(request)
    return await _run(dispatch.report, data.get("work_item_ref"), data.get("lease_token"), data.get("outcome"),
                      note=data.get("note", ""), operation_receipt_ref=data.get("operation_receipt_ref"))
