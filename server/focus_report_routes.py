"""Route for the local focus report (dashboard session only)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pathlib import Path

from fastapi.responses import FileResponse, JSONResponse

from . import focus_report
from .local_auth import dashboard_session_valid

router = APIRouter()
_SCRIPT = Path(__file__).resolve().parents[1] / "dashboard" / "focus_report.js"


@router.get("/focus-report.js")
def focus_report_script() -> FileResponse:
    return FileResponse(_SCRIPT, media_type="application/javascript")


@router.get("/v1/focus-report")
def focus_report_route(request: Request, start: str = Query(max_length=10), end: str = Query(max_length=10),
                       tz: str = Query(default="UTC", max_length=64), max_gap_seconds: int = 120,
                       ignore_detours_seconds: int = 30):
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(403, "Local dashboard session required")
    try:
        result = focus_report.report(start=start, end=end, tz=tz, max_gap_seconds=max_gap_seconds,
                                     ignore_detours_seconds=ignore_detours_seconds)
    except focus_report.FocusReportError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
