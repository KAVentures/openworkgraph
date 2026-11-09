"""Routes for the local time-allocation report (dashboard session only)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse, Response

from . import time_allocation
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "time_allocation.js"


@router.get("/time-allocation.js")
def time_allocation_script() -> FileResponse:
    return FileResponse(_SCRIPT, media_type="application/javascript")


def _require_dashboard(request: Request) -> None:
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")


@router.get("/v1/time-allocation")
def time_allocation_report(request: Request, start: str = Query(max_length=10), end: str = Query(max_length=10),
                           tz: str = Query(default="UTC", max_length=64), titles: bool = False,
                           format: str = Query(default="json", pattern="^(json|csv)$")):
    _require_dashboard(request)
    try:
        result = time_allocation.report(start=start, end=end, tz=tz, include_titles=titles and format == "json")
    except time_allocation.TimeAllocationError as exc:
        raise HTTPException(422, str(exc)) from None
    if format == "csv":
        return Response(time_allocation.to_csv(result), media_type="text/csv", headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="time-allocation-{result["start"]}-to-{result["end"]}.csv"'})
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
