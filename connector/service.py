from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from collector.identity import load_or_create_identity
from .config import load_device_token, load_gateway_settings, store_device_token
from .state import SyncState


def _paths(config_path: Path) -> tuple[Path, Path, Path]:
    root = config_path.resolve().parent
    data_dir = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data" / "live"))
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", root / "data" / "auth"))
    return root, data_dir, auth_dir


def _read_config(config_path: Path) -> dict[str, Any]:
    try:
        value = json.loads(config_path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _write_config(config_path: Path, value: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, config_path)


def normalize_gateway_url(value: str, *, allow_insecure_http: bool = False) -> str:
    url = str(value or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Gateway URL must be an http(s) URL with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Gateway URL must not contain embedded credentials")
    host = parsed.hostname.lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not loopback and not allow_insecure_http:
        raise ValueError("Non-local Gateway connections require HTTPS")
    return url


def enroll_endpoint(
    config_path: Path,
    *,
    gateway_url: str,
    organization_id: str,
    actor_id: str = "",
    enrollment_token: str,
    verify_tls: bool = True,
    allow_insecure_http: bool = False,
    device_id: str = "",
) -> dict[str, Any]:
    """Enroll this endpoint without creating an OpenWorkGraph cloud account.

    The one-time/admin enrollment token is used only for this request and is never
    written to config. The returned device credential is stored in the local auth
    directory and the endpoint's config is updated atomically.
    """
    gateway_url = normalize_gateway_url(gateway_url, allow_insecure_http=allow_insecure_http)
    organization_id = str(organization_id or "").strip()
    actor_id = str(actor_id or "").strip()
    enrollment_token = str(enrollment_token or "").strip()
    if not organization_id:
        raise ValueError("organization_id is required")
    if not enrollment_token:
        raise ValueError("enrollment token is required")

    _, data_dir, auth_dir = _paths(config_path)
    identity = load_or_create_identity(data_dir, {"device_id": device_id, "sensor_id": ""})
    resolved_device_id = str(device_id or identity.get("device_id") or "").strip()
    if not resolved_device_id:
        raise RuntimeError("Could not determine a device ID")

    with httpx.Client(timeout=15, verify=bool(verify_tls)) as client:
        response = client.post(
            f"{gateway_url}/v1/devices/enroll",
            headers={"Authorization": f"Bearer {enrollment_token}"},
            json={
                "organization_id": organization_id,
                "actor_id": actor_id,
                "device_id": resolved_device_id,
            },
        )
        response.raise_for_status()
        payload = response.json()

    token = str(payload.get("token") or "").strip()
    if not token:
        raise RuntimeError("Gateway did not return a device credential")

    credential_path = auth_dir / ".gateway_device_token"
    store_device_token(credential_path, token)

    root = _read_config(config_path)
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    gateway = dict(gateway)
    gateway.update({
        "enabled": True,
        "url": gateway_url,
        "verify_tls": bool(verify_tls),
    })
    gateway.setdefault("batch_size", 100)
    gateway.setdefault("poll_seconds", 2)
    gateway.setdefault("policy_refresh_seconds", 60)
    gateway.setdefault("local_policy", {
        "share_excluded": False,
        "share_window_titles": True,
        "share_metadata": True,
        "allowed_event_types": [],
        "strip_metadata_keys": [],
    })
    root["gateway"] = gateway
    _write_config(config_path, root)

    state = SyncState(data_dir / "gateway_sync_state.db")
    state.set_bool("sharing_paused", False)
    state.set("status", "not_started")
    state.set("last_error", "")
    state.set("gateway_url", gateway_url)

    return {
        "gateway_url": gateway_url,
        "organization_id": str(payload.get("organization_id") or organization_id),
        "actor_id": str(payload.get("actor_id") or actor_id),
        "device_id": str(payload.get("device_id") or resolved_device_id),
        "enrolled": True,
        "sharing_paused": False,
        "credential_exposed": False,
    }


def disconnect_endpoint(config_path: Path, *, require_remote_revoke: bool = True) -> dict[str, Any]:
    """Revoke the current device credential and return the endpoint to local-only mode."""
    _, data_dir, auth_dir = _paths(config_path)
    settings = load_gateway_settings(config_path, auth_dir=auth_dir)
    token = load_device_token(settings)

    if token and settings.url:
        try:
            with httpx.Client(timeout=10, verify=settings.verify_tls) as client:
                response = client.post(
                    f"{settings.url}/v1/device/revoke",
                    headers={"Authorization": f"Bearer {token}"},
                )
                response.raise_for_status()
        except Exception:
            if require_remote_revoke:
                raise

    try:
        settings.token_file.unlink(missing_ok=True)
    except Exception as exc:
        raise RuntimeError("Could not remove the local Gateway device credential") from exc

    root = _read_config(config_path)
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    gateway = dict(gateway)
    gateway["enabled"] = False
    root["gateway"] = gateway
    _write_config(config_path, root)

    state = SyncState(data_dir / "gateway_sync_state.db")
    state.set_bool("sharing_paused", True)
    state.set("status", "local_only")
    state.set("last_error", "")

    return {
        "mode": "local_only",
        "gateway_enabled": False,
        "enrolled": False,
        "sharing_paused": True,
        "credential_exposed": False,
    }
