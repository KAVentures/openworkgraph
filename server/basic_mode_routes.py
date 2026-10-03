from __future__ import annotations

"""Basic/Advanced dashboard: the Privacy tab's never-record lists and its script.

Imported last by the enterprise runner so its script runs after every other
dashboard layer and can arrange what they built.
"""

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from . import capture_exclusions
from .main import ROOT
from .update_check import update_status
from .secure_app import app


def get_capture_exclusions() -> dict[str, Any]:
    return capture_exclusions.current()


async def put_capture_exclusions(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
        return capture_exclusions.update(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def basic_mode_script() -> Response:
    path = ROOT / "dashboard" / "basic_mode.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


async def _inject_basic_mode_script(request: Request, call_next):
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
    marker = '<script src="/basic-mode.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


if not getattr(app.state, "owg_basic_mode_installed", False):
    app.add_api_route("/v1/capture-exclusions", get_capture_exclusions, methods=["GET"])
    app.add_api_route("/v1/capture-exclusions", put_capture_exclusions, methods=["PUT"])
    app.add_api_route("/basic-mode.js", basic_mode_script, methods=["GET"])
    app.add_api_route("/v1/update-status", update_status, methods=["GET"])
    app.middleware("http")(_inject_basic_mode_script)
    app.state.owg_basic_mode_installed = True


__all__ = ["basic_mode_script", "get_capture_exclusions", "put_capture_exclusions", "update_status"]
