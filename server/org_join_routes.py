from __future__ import annotations

"""Employee-side organization joining.

Manual join is consentful: preview first, then explicit confirmation. Managed
setup is for IT-managed devices and is always visible in the employee dashboard;
it never changes what OpenWorkGraph captures locally, only whether eligible
privacy-hardened evidence may synchronize to the configured organization.
"""

import getpass
import json
import os
import platform
import threading
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel

from connector.runtime import restart_sync_worker
from connector.service import enroll_endpoint
from shared.join_code import decode_join_code
from .enterprise_app import CONFIG_PATH, _combined_status, _demo_mode, app

_lock = threading.Lock()
_managed_state: dict[str, Any] = {"managed": False}


class JoinCodeRequest(BaseModel):
    join_code: str


class JoinRequest(BaseModel):
    join_code: str
    # Ignored for personal invitations: the Gateway binds the invited employee.
    actor_id: str = ""
    accept_sharing: bool = False


def _preview(code: str) -> dict[str, Any]:
    info = decode_join_code(code)
    try:
        with httpx.Client(timeout=10.0, verify=True) as client:
            response = client.get(
                f"{info['gateway_url']}/v1/devices/join-preview",
                headers={"Authorization": f"Bearer {info['token']}"},
            )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach your organization's Gateway at {info['gateway_url']}",
        ) from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        raise HTTPException(status_code=400, detail=detail or "This join code was not accepted by the Gateway")
    data = response.json()
    organization_id = str(data.get("organization_id") or info["organization_id"])
    if organization_id != info["organization_id"]:
        raise HTTPException(status_code=400, detail="The Gateway returned a different organization than the join code")
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {"locked": False}
    identity = dict(identity)
    if identity.get("locked") and identity.get("require_sso"):
        # Built from the Gateway address in the join code, never from Gateway-supplied URLs.
        from urllib.parse import quote
        identity["verify_url"] = f"{info['gateway_url']}/join/verify#code={quote(re_code(code))}"
    identity.pop("verify_path", None)
    return {
        "organization_name": str(data.get("organization_name") or info["organization_name"]),
        "organization_id": organization_id,
        "gateway_url": info["gateway_url"],
        "identity": identity,
        **{k: data.get(k) for k in ("expires_at", "seats_left", "sharing", "never_shared", "you_can")},
    }


def re_code(code: str) -> str:
    return "".join(str(code or "").split())


def _join(code: str, actor_id: str) -> dict[str, Any]:
    info = decode_join_code(code)
    actor = str(actor_id or "").strip()
    if not actor or len(actor) > 320:
        raise ValueError("enter your work email or username")
    result = enroll_endpoint(
        CONFIG_PATH,
        gateway_url=info["gateway_url"],
        organization_id=info["organization_id"],
        actor_id=actor,
        enrollment_token=info["token"],
        verify_tls=True,
        allow_insecure_http=bool(info["local_gateway"]),
    )
    restart_sync_worker(CONFIG_PATH)
    return result


@app.post("/v1/org-join/preview")
def org_join_preview(request: JoinCodeRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Joining an organization is disabled in demo mode")
    try:
        return _preview(request.join_code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/org-join")
def org_join(request: JoinRequest) -> dict[str, Any]:
    if _demo_mode():
        raise HTTPException(status_code=409, detail="Joining an organization is disabled in demo mode")
    if not request.accept_sharing:
        raise HTTPException(status_code=400, detail="Review what will be shared and confirm before joining")
    try:
        # Re-preview inside the lock so a changed/revoked policy/link is noticed
        # immediately before the enrollment request is made.
        with _lock:
            preview = _preview(request.join_code)
            identity = preview.get("identity") or {}
            if identity.get("locked"):
                if identity.get("require_sso") and not identity.get("sso_verified"):
                    raise HTTPException(
                        status_code=400,
                        detail="Confirm it's you with your company account first (use the Confirm button), then join.",
                    )
                actor = str(identity.get("actor_id") or identity.get("email") or "")
            else:
                actor = request.actor_id
            result = _join(request.join_code, actor)
        return {**result, "organization_name": preview["organization_name"], **_combined_status()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Joining failed: {str(exc)[:300]}") from exc


@app.post("/v1/org-me-link")
def org_me_link() -> dict[str, Any]:
    """One-time link to the Gateway page showing what the organization holds about you."""
    from connector.config import load_device_token, load_gateway_settings
    from connector.control import _paths

    if _demo_mode():
        raise HTTPException(status_code=409, detail="Not available in demo mode")
    _data_dir, auth_dir = _paths(CONFIG_PATH)
    settings = load_gateway_settings(CONFIG_PATH, auth_dir=auth_dir)
    token = load_device_token(settings)
    if not settings.enabled or not settings.url or not token:
        raise HTTPException(status_code=409, detail="This computer is not connected to an organization")
    try:
        with httpx.Client(timeout=10.0, verify=settings.verify_tls) as client:
            response = client.post(f"{settings.url}/v1/devices/me-link", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not reach your organization's Gateway at {settings.url}") from exc
    if response.status_code != 200:
        try:
            detail = response.json().get("detail")
        except Exception:
            detail = None
        raise HTTPException(status_code=502, detail=detail or "The Gateway did not accept this computer")
    path = str(response.json().get("path") or "")
    if not path.startswith("/me#code="):
        raise HTTPException(status_code=502, detail="Unexpected response from the Gateway")
    return {"url": settings.url.rstrip("/") + path, "expires_in_seconds": response.json().get("expires_in_seconds")}


# --- Managed (zero-touch) setup -------------------------------------------------------

def managed_config_path() -> Path:
    explicit = os.getenv("OWG_MANAGED_CONFIG", "").strip()
    if explicit:
        return Path(explicit)
    system = platform.system()
    if system == "Darwin":
        return Path("/Library/Application Support/OpenWorkGraph/managed.json")
    if system == "Windows":
        return Path(os.getenv("PROGRAMDATA", r"C:\ProgramData")) / "OpenWorkGraph" / "managed.json"
    return Path("/etc/openworkgraph/managed.json")


def _managed_actor(config: dict[str, Any]) -> str:
    explicit = str(config.get("actor_id") or "").strip()
    if explicit:
        return explicit
    user = getpass.getuser().strip()
    domain = str(config.get("email_domain") or "").strip().lstrip("@")
    return f"{user}@{domain}" if domain else user


def apply_managed_config() -> dict[str, Any]:
    """Join automatically when IT deployed managed.json; never hide that state."""
    path = managed_config_path()
    state: dict[str, Any] = {"managed": False, "config_path": str(path)}
    if _demo_mode() or not path.is_file():
        _managed_state.clear()
        _managed_state.update(state)
        return state
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        code = str(config.get("join_code") or "")
        info = decode_join_code(code)
        preview = _preview(code)
    except Exception as exc:
        state.update({"managed": True, "status": "invalid_config", "error": str(exc)[:200]})
        _managed_state.clear()
        _managed_state.update(state)
        return state

    state.update({
        "managed": True,
        "organization_name": preview["organization_name"],
        "organization_id": info["organization_id"],
        "gateway_url": info["gateway_url"],
        "sharing_preview": preview.get("sharing"),
    })
    current = _combined_status()
    if current.get("enrolled"):
        same_gateway = str(current.get("gateway_url") or "").rstrip("/") == info["gateway_url"]
        same_org = str(current.get("organization_id") or "") == info["organization_id"]
        if same_gateway and same_org:
            state["status"] = "joined"
        else:
            state.update({
                "status": "conflict_existing_enrollment",
                "error": "This computer is already enrolled with a different organization or Gateway. Disconnect it before managed setup can join this one.",
            })
    else:
        try:
            with _lock:
                _join(code, _managed_actor(config))
            state["status"] = "joined"
        except Exception as exc:
            state.update({"status": "join_failed", "error": str(exc)[:200]})

    _managed_state.clear()
    _managed_state.update(state)
    return state


@app.get("/org-join.js", include_in_schema=False)
def org_join_script() -> Response:
    path = Path(__file__).resolve().parents[1] / "dashboard" / "org_join.js"
    return Response(
        path.read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/v1/managed-status")
def get_managed_status() -> dict[str, Any]:
    return dict(_managed_state) if _managed_state.get("config_path") else apply_managed_config()


@app.middleware("http")
async def inject_org_join_dashboard(request: Request, call_next):
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
    marker = '<script src="/org-join.js"></script>'
    if marker not in text:
        text = text.replace("</body>", marker + "\n</body>")
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return HTMLResponse(content=text, status_code=response.status_code, headers=headers)


def start_managed_setup_in_background() -> None:
    """Called by the runner at start; never blocks local capture or dashboard."""
    threading.Thread(target=apply_managed_config, name="owg-managed-setup", daemon=True).start()


__all__ = [
    "apply_managed_config",
    "managed_config_path",
    "start_managed_setup_in_background",
    "org_join",
    "org_join_preview",
]
