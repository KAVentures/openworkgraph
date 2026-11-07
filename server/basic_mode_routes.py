from __future__ import annotations

"""Basic/Advanced dashboard: the Privacy tab's never-record lists and its script.

Imported last by the enterprise runner so its script runs after every other
dashboard layer and can arrange what they built.
"""

from typing import Any

from starlette.concurrency import run_in_threadpool

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from . import capture_exclusions, screen_capture_settings
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


def get_screen_capture() -> dict[str, Any]:
    return screen_capture_settings.current()


async def put_screen_capture(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
        return screen_capture_settings.update(payload)
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_local_backups() -> dict[str, Any]:
    from . import local_backup
    return local_backup.list_backups()


async def post_local_backup(request: Request) -> dict[str, Any]:
    from . import local_backup
    try:
        payload = await request.json() if await request.body() else {}
        if not isinstance(payload, dict):
            raise local_backup.BackupError("expected an object")
        passphrase = payload.get("passphrase")
        if passphrase is not None and not isinstance(passphrase, str):
            raise local_backup.BackupError("passphrase must be text")
        if payload.get("verify"):
            return local_backup.verify_in_folder(payload.get("verify"), passphrase)
        return local_backup.create_in_folder(passphrase)
    except local_backup.BackupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def post_local_backup_preflight(request: Request) -> Response:
    """Read-only plan for one backup already in OWG's configured local backup folder.

    This is intentionally a dashboard-only endpoint, not a source/agent API or
    an online restore. Encrypted archives must use the offline CLI so secrets
    never enter browser requests. Names are chosen from the bounded local list,
    not arbitrary user-supplied filesystem paths.
    """
    from . import local_backup
    import json

    _require_dashboard_session(request)
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "JSON backup selection required")
    raw = bytearray()
    try:
        async for chunk in request.stream():
            if len(raw) + len(chunk) > 512:
                raise HTTPException(413, "Backup selection is too large")
            raw.extend(chunk)
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(422, "Invalid backup selection") from None
        if (not isinstance(value, dict) or set(value) != {"name"}
                or not isinstance(value["name"], str) or len(value["name"]) > 160
                or not value["name"].startswith("owg-backup-")
                or any(ord(c) in (0, 47, 58, 92) for c in value["name"])):
            raise HTTPException(422, "Choose one listed local backup by name")
        selected = next((item for item in local_backup.list_backups()["backups"]
                         if item["name"] == value["name"]), None)
        if selected is None:
            raise HTTPException(404, "Selected local backup was not found")
        if selected["encrypted"]:
            raise HTTPException(422, "Encrypted backups require offline CLI preflight with a passphrase environment variable")
        try:
            result = await run_in_threadpool(
                local_backup.plan_restore, local_backup.backup_folder() / value["name"]
            )
        except local_backup.BackupError as exc:
            raise HTTPException(422, str(exc)) from None
        from fastapi.responses import JSONResponse
        return JSONResponse(result, headers={"Cache-Control": "no-store"})
    finally:
        raw.clear()


def _require_dashboard_session(request: Request) -> None:
    from .local_auth import dashboard_session_valid
    auth = str(request.headers.get("authorization") or "")
    if not auth.lower().startswith("owg-session ") or not dashboard_session_valid(auth[len("owg-session "):].strip()):
        raise HTTPException(status_code=403, detail="Local dashboard session required to change capture depth")


def get_capture_depth() -> dict[str, Any]:
    from . import capture_depth
    return capture_depth.public()


async def put_capture_depth(request: Request) -> dict[str, Any]:
    from . import capture_depth
    _require_dashboard_session(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="capture depth must be JSON") from None
    try:
        return capture_depth.save(payload)
    except capture_depth.CaptureDepthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


async def delete_capture_detail(request: Request) -> dict[str, Any]:
    """Delete recorded high-fidelity interaction detail now: all of it, or older than N days."""
    from . import capture_depth
    _require_dashboard_session(request)
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="deletion must be JSON") from None
    if not isinstance(payload, dict) or set(payload) - {"older_than_days", "confirm"} or payload.get("confirm") is not True:
        raise HTTPException(status_code=400, detail="send confirm: true, and optionally older_than_days")
    days = payload.get("older_than_days")
    if days is not None and (not isinstance(days, int) or isinstance(days, bool) or not 0 <= days <= 3650):
        raise HTTPException(status_code=400, detail="older_than_days must be 0 to 3650")
    return capture_depth.delete_high_fidelity(older_than_days=days)


def get_pointer_heatmap(request: Request, hostname: str | None = None, pathname: str | None = None,
                        hours: int = 168) -> dict[str, Any]:
    from . import capture_depth
    _require_dashboard_session(request)
    return capture_depth.pointer_heatmap(hostname=hostname[:253] if hostname else None,
                                         pathname=pathname[:2048] if pathname else None,
                                         hours=max(1, min(int(hours), 24 * 365)))


def get_capture_coverage(hours: int = 168) -> dict[str, Any]:
    from .interaction_evidence import coverage_manifest
    return coverage_manifest(max(1, min(int(hours), 24 * 90)))


def get_friction_candidates(request: Request, hours: int = 24) -> dict[str, Any]:
    from .interaction_evidence import friction_report
    _require_dashboard_session(request)
    return friction_report(max(1, min(int(hours), 24 * 31)))


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
    marker = '<script src="/basic-mode.js"></script>\n<script src="/analysis-handoff.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(text, status_code=response.status_code, headers=headers)


if not getattr(app.state, "owg_basic_mode_installed", False):
    app.add_api_route("/v1/capture-exclusions", get_capture_exclusions, methods=["GET"])
    app.add_api_route("/v1/capture-exclusions", put_capture_exclusions, methods=["PUT"])
    app.add_api_route("/v1/screen-capture", get_screen_capture, methods=["GET"])
    app.add_api_route("/v1/screen-capture", put_screen_capture, methods=["PUT"])
    app.add_api_route("/v1/local-backups", get_local_backups, methods=["GET"])
    app.add_api_route("/v1/local-backups", post_local_backup, methods=["POST"])
    app.add_api_route("/v1/local-backups/preflight", post_local_backup_preflight, methods=["POST"])
    app.add_api_route("/v1/capture-depth", get_capture_depth, methods=["GET"])
    app.add_api_route("/v1/capture-depth", put_capture_depth, methods=["PUT"])
    app.add_api_route("/v1/capture-coverage", get_capture_coverage, methods=["GET"])
    app.add_api_route("/v1/capture-coverage/friction", get_friction_candidates, methods=["GET"])
    app.add_api_route("/v1/capture-coverage/heatmap", get_pointer_heatmap, methods=["GET"])
    app.add_api_route("/v1/capture-depth/delete", delete_capture_detail, methods=["POST"])
    app.add_api_route("/basic-mode.js", basic_mode_script, methods=["GET"])
    app.add_api_route("/v1/update-status", update_status, methods=["GET"])
    from .analysis_handoff import analysis_preview
    app.add_api_route("/v1/analysis-preview", analysis_preview, methods=["GET"])
    app.add_api_route("/analysis-handoff.js", lambda: Response(
        (ROOT / "dashboard" / "analysis_handoff.js").read_text(encoding="utf-8"),
        media_type="application/javascript"), methods=["GET"])
    app.middleware("http")(_inject_basic_mode_script)
    app.state.owg_basic_mode_installed = True


__all__ = ["basic_mode_script", "get_capture_exclusions", "get_screen_capture", "put_capture_exclusions",
           "put_screen_capture", "update_status"]
