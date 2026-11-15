from __future__ import annotations

"""Dashboard routes for opt-in local schedules (see local_scheduler)."""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from . import local_scheduler as scheduler
from . import signal_notification_delivery as notification_delivery
from .local_auth import dashboard_session_valid

router = APIRouter()


def _require_dashboard(request: Request) -> None:
    raw = str(request.headers.get("authorization") or "")
    if not raw.lower().startswith("owg-session ") or not dashboard_session_valid(raw[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="explicit local dashboard session required")


async def _body(request: Request) -> dict:
    if len(await request.body()) > 1024:
        raise HTTPException(status_code=413, detail="request too large")
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="expected an object")
    return data


async def _run(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except scheduler.ScheduleError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/v1/schedules")
async def schedules(request: Request):
    _require_dashboard(request)
    return await _run(scheduler.status)


@router.post("/v1/schedules")
async def create_schedule(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return await _run(scheduler.create, data.get("kind"), data.get("target_id"),
                      interval_minutes=data.get("interval_minutes"),
                      confirm_scheduled_runs=data.get("confirm_scheduled_runs"),
                      confirm_unencrypted_local_backup=data.get("confirm_unencrypted_local_backup"),
                      quiet_hours=data.get("quiet_hours"))


@router.post("/v1/schedules/update")
async def update_schedule(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    params = {"enabled": data.get("enabled"), "interval_minutes": data.get("interval_minutes"),
              "confirm_current_signal_rules": data.get("confirm_current_signal_rules", False)}
    if "quiet_hours" in data:
        # Omitting quiet_hours preserves the current window; explicit JSON null
        # clears it. A false/string value must never silently disable consent.
        params["quiet_hours"] = data["quiet_hours"]
    return await _run(scheduler.update, data.get("schedule_id"), **params)


@router.post("/v1/schedules/delete")
async def delete_schedule(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return {"removed": await _run(scheduler.delete, data.get("schedule_id"))}


async def _delivery_call(fn, *args, **kwargs):
    try:
        return await asyncio.to_thread(fn, *args, **kwargs)
    except notification_delivery.DeliveryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("/v1/schedules/signal-delivery/{log_id}")
async def signal_delivery_status(request: Request, log_id: str):
    _require_dashboard(request)
    return await _delivery_call(notification_delivery.status, log_id)


@router.post("/v1/schedules/signal-delivery/{log_id}")
async def create_signal_delivery(request: Request, log_id: str):
    _require_dashboard(request)
    data = await _body(request)
    if set(data) != {"receiver_url", "confirm_external_delivery", "confirm_counts_only"}:
        raise HTTPException(status_code=422, detail="receiver URL and both separate acknowledgements required")
    return await _delivery_call(
        notification_delivery.configure, log_id, data["receiver_url"],
        confirm_external_delivery=data["confirm_external_delivery"],
        confirm_counts_only=data["confirm_counts_only"])


@router.post("/v1/schedules/signal-delivery/{log_id}/resume")
async def resume_signal_delivery(request: Request, log_id: str):
    _require_dashboard(request)
    data = await _body(request)
    if set(data) != {"confirm_external_delivery"}:
        raise HTTPException(status_code=422, detail="explicit resumed delivery confirmation required")
    return await _delivery_call(notification_delivery.resume, log_id,
                                confirm_external_delivery=data["confirm_external_delivery"])


@router.post("/v1/schedules/signal-delivery/{log_id}/remove")
async def remove_signal_delivery(request: Request, log_id: str):
    _require_dashboard(request)
    data = await _body(request)
    if set(data) != {"confirm_remove"} or data["confirm_remove"] is not True:
        raise HTTPException(status_code=403, detail="explicit channel deletion confirmation required")
    return await _delivery_call(notification_delivery.remove, log_id)
