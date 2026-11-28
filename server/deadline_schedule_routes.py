"""Static, browser-local deadline schedule review assets."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_DASH = Path(__file__).resolve().parents[1] / "dashboard"


@router.get("/deadline-schedule.js")
def core():
    return FileResponse(_DASH / "deadline_schedule.js", media_type="application/javascript",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


@router.get("/deadline-schedule-ui.js")
def ui():
    return FileResponse(_DASH / "deadline_schedule_ui.js", media_type="application/javascript",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
