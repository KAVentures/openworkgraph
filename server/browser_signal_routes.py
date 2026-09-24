from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from . import main as core
from .browser_signal_settings import public_settings, save_settings
from .main import ROOT
from .secure_app import app


def browser_context_with_signal_settings() -> dict[str, Any]:
    return {
        "organization_id": str(core.COLLECTOR_STATUS.get("organization_id") or ""),
        "actor_id": str(core.COLLECTOR_STATUS.get("actor_id") or ""),
        "device_id": str(core.COLLECTOR_STATUS.get("device_id") or ""),
        "work_session_id": str(core.COLLECTOR_STATUS.get("session_id") or ""),
        "signal_settings": public_settings()["settings"],
    }


def get_browser_signal_settings() -> dict[str, Any]:
    return public_settings()


async def update_browser_signal_settings(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    current = public_settings()["settings"]
    if isinstance(payload, dict):
        for key in ("performance_timing", "file_upload_category"):
            if key in payload:
                current[key] = bool(payload[key])
    save_settings(current)
    return public_settings()


def browser_signal_ui_script() -> Response:
    path = ROOT / "dashboard" / "browser_signals.js"
    return Response(path.read_text(encoding="utf-8"), media_type="application/javascript")


def _replace_route(path: str, method: str, endpoint: Any) -> None:
    wanted = method.upper()
    app.router.routes[:] = [
        route for route in app.router.routes
        if not (getattr(route, "path", None) == path and wanted in {str(x).upper() for x in (getattr(route, "methods", None) or set())})
    ]
    app.add_api_route(path, endpoint, methods=[wanted])


async def _inject_script(request: Request, call_next):
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
    marker = '<script src="/browser-signals.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


if not getattr(app.state, "owg_v058_browser_signal_routes_installed", False):
    _replace_route("/v1/browser-context", "GET", browser_context_with_signal_settings)
    app.add_api_route("/v1/browser-signal-settings", get_browser_signal_settings, methods=["GET"])
    app.add_api_route("/v1/browser-signal-settings", update_browser_signal_settings, methods=["POST"])
    app.add_api_route("/browser-signals.js", browser_signal_ui_script, methods=["GET"])
    app.middleware("http")(_inject_script)
    app.state.owg_v058_browser_signal_routes_installed = True
