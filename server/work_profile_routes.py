from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .privacy_pipeline import redact_for_display
from .secure_app import app
from .work_profile_service import SELF_TAG_CATEGORIES, add_self_tag, compute_work_profile
from .main import ROOT


def _dashboard_safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove rich context echoes that the human localhost dashboard does not need."""
    out = dict(profile)
    out["navigation_hunting_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator"}}
        for item in (profile.get("navigation_hunting_candidates") or [])
    ]
    out["rapid_click_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator", "target_label"}}
        for item in (profile.get("rapid_click_candidates") or [])
    ]
    out["dashboard_data_layer"] = "content_minimized"
    return out


@app.get("/v1/work-profile")
def work_profile(scope: str = "current") -> dict[str, Any]:
    try:
        return redact_for_display(_dashboard_safe_profile(compute_work_profile(scope=scope)))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/v1/self-tags/categories")
def self_tag_categories() -> dict[str, Any]:
    return {"categories": list(SELF_TAG_CATEGORIES), "voluntary": True}


@app.post("/v1/self-tags")
async def create_self_tag(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body must be an object")
    category = str(payload.get("category") or "").strip()
    now = datetime.now(timezone.utc)
    started_at = payload.get("started_at")
    ended_at = payload.get("ended_at")
    if not started_at or not ended_at:
        try:
            minutes = max(1, min(int(payload.get("minutes_back") or 15), 240))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="minutes_back must be an integer") from exc
        end = now
        start = end - timedelta(minutes=minutes)
        started_at = start.isoformat()
        ended_at = end.isoformat()
    try:
        tag = add_self_tag(
            category=category,
            started_at=str(started_at),
            ended_at=str(ended_at),
            session_id=str(payload.get("session_id") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return redact_for_display({"status": "ok", "tag": tag})


@app.get("/work-profile.js")
def work_profile_script() -> Response:
    path = ROOT / "dashboard" / "work_profile.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


@app.middleware("http")
async def inject_work_profile_script(request: Request, call_next):
    response = await call_next(request)
    if request.method.upper() != "GET" or request.url.path != "/" or response.status_code != 200:
        return response
    if "text/html" not in str(response.headers.get("content-type") or ""):
        return response
    try:
        if hasattr(response, "body_iterator"):
            chunks = [chunk async for chunk in response.body_iterator]
            body = b"".join(chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8") for chunk in chunks)
        else:
            body = bytes(getattr(response, "body", b""))
        text = body.decode("utf-8")
    except Exception:
        return response
    marker = '<script src="/work-profile.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(content=text, status_code=response.status_code, headers=headers)
