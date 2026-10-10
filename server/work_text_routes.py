from __future__ import annotations

"""Local browser-content routes, deliberately outside Gateway and event ingestion."""

from fastapi import Request
from fastapi.responses import JSONResponse

from . import work_text_capture as work_text
from .secure_app import app


@app.get("/v1/work-text/policy")
def work_text_policy() -> dict:
    return {
        **work_text.get_policy(),
        "gateway_shared": False,
        "browser_only": True,
        "content_capture_is_separate_from_ai_access": True,
    }


@app.post("/v1/work-text/policy")
async def save_work_text_policy(request: Request):
    # The existing local secure_app master guard requires an authenticated
    # dashboard/API capability; an MCP AI request cannot change this policy.
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").lower() == "ai":
        return JSONResponse({"detail": "AI cannot grant itself text access."}, status_code=403)
    try:
        policy = work_text.set_policy(await request.json())
        return {**policy, "gateway_shared": False}
    except (ValueError, TypeError) as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)


@app.post("/v1/work-text/ingest")
async def ingest_work_text(request: Request):
    try:
        payload = await request.json()
        return work_text.ingest(payload)
    except PermissionError:
        return JSONResponse({"detail": "Work-content capture declined."}, status_code=403)
    except (ValueError, TypeError):
        return JSONResponse({"detail": "Invalid work-content snapshot."}, status_code=400)


@app.get("/v1/work-text/ai")
def read_work_text(request: Request, limit: int = 20):
    # Requires standard local API bearer *and* the MCP's AI context marker;
    # the existing secure_app middleware already checks the bearer.
    if str(request.headers.get("X-OpenWorkGraph-Context") or "").lower() != "ai":
        return JSONResponse({"detail": "Local AI context required"}, status_code=403)
    try:
        return work_text.recent_for_ai(limit=limit)
    except PermissionError:
        return JSONResponse({"detail": "Work-text AI access is disabled"}, status_code=403)
