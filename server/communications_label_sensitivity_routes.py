"""Browser-local aggregate annotation sensitivity assets."""
from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import FileResponse

router = APIRouter()
_DASH = Path(__file__).resolve().parents[1] / "dashboard"


@router.get("/communications-label-sensitivity.js")
def core():
    return FileResponse(_DASH / "communications_label_sensitivity.js", media_type="application/javascript",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})


@router.get("/communications-label-sensitivity-ui.js")
def ui():
    return FileResponse(_DASH / "communications_label_sensitivity_ui.js", media_type="application/javascript",
                        headers={"Cache-Control":"no-store","X-Content-Type-Options":"nosniff"})
