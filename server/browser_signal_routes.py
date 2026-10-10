from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse, Response

from . import main as core
from .browser_signal_settings import SETTING_KEYS, apply_profile, public_settings, save_settings
from .main import ROOT
from .secure_app import app


# Browser privacy hardening receives core._runtime_config(). Merge these local
# signal settings there so the server, not only the extension UI, enforces the
# selected privacy profile on every event and on legacy-row hardening.
_BASE_RUNTIME_CONFIG = core._runtime_config
if not getattr(core, "_owg_browser_signal_runtime_wrapped", False):
    def _runtime_config_with_signal_settings() -> dict[str, Any]:
        cfg = dict(_BASE_RUNTIME_CONFIG())
        cfg.update(public_settings()["settings"])
        return cfg

    core._runtime_config = _runtime_config_with_signal_settings
    core._owg_browser_signal_runtime_wrapped = True


def browser_context_with_signal_settings() -> dict[str, Any]:
    runtime = core._runtime_config()
    return {
        "organization_id": str(core.COLLECTOR_STATUS.get("organization_id") or ""),
        "actor_id": str(core.COLLECTOR_STATUS.get("actor_id") or ""),
        "device_id": str(core.COLLECTOR_STATUS.get("device_id") or ""),
        "work_session_id": str(core.COLLECTOR_STATUS.get("session_id") or ""),
        "signal_settings": public_settings()["settings"],
        # Separate explicit, off-by-default content collection permission.
        "work_text_capture": __import__("server.work_text_capture", fromlist=["get_policy"]).get_policy()["capture_enabled"],
        # These local patterns are supplied to the paired extension only so it can
        # fail closed before an optional rich locator enters its retry queue. The
        # server applies the same policy again at ingest.
        "excluded_browser_host_patterns": list(runtime.get("excluded_browser_host_patterns") or []),
        "excluded_title_patterns": list(runtime.get("excluded_title_patterns") or []),
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
        if payload.get("profile") in {"privacy_first", "context", "rich_enterprise"}:
            current = apply_profile(str(payload["profile"]), current)
        for key in SETTING_KEYS:
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
