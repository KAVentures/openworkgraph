from __future__ import annotations

"""Dashboard routes for opt-in local schedules (see local_scheduler)."""
import asyncio

from fastapi import APIRouter, HTTPException, Request

from . import local_scheduler as scheduler
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
                      confirm_scheduled_runs=data.get("confirm_scheduled_runs"))


@router.post("/v1/schedules/update")
async def update_schedule(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return await _run(scheduler.update, data.get("schedule_id"), enabled=data.get("enabled"),
                      interval_minutes=data.get("interval_minutes"))


@router.post("/v1/schedules/delete")
async def delete_schedule(request: Request):
    _require_dashboard(request)
    data = await _body(request)
    return {"removed": await _run(scheduler.delete, data.get("schedule_id"))}
