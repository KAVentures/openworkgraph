"""Static offline capacity transition workbench; never receives report data."""
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"


@router.get("/capacity-transition")
def capacity_transition_page() -> FileResponse:
    return FileResponse(
        _DASHBOARD / "capacity_transition.html", media_type="text/html",
        headers={"Cache-Control": "no-store",
                 "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"},
    )


@router.get("/capacity_transition.js")
def capacity_transition_script() -> FileResponse:
    return FileResponse(_DASHBOARD / "capacity_transition.js", media_type="application/javascript")


@router.get("/capacity_transition_ui.js")
def capacity_transition_ui() -> FileResponse:
    return FileResponse(_DASHBOARD / "capacity_transition_ui.js", media_type="application/javascript")
