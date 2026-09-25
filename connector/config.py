from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GatewaySyncSettings:
    enabled: bool
    url: str
    verify_tls: bool
    batch_size: int
    poll_seconds: float
    policy_refresh_seconds: float
    local_policy: dict[str, Any]
    token_file: Path
    managed_declared_policy_enabled: bool = False
    managed_declared_policy_organization_id: str = ""
    managed_declared_policy_trusted_keys: dict[str, str] = field(default_factory=dict)
    managed_declared_policy_refresh_seconds: float = 60.0
    managed_declared_policy_target_file: Path | None = None


def _trusted_keys(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, str] = {}
    for key, raw in list(value.items())[:32]:
        key_id = str(key or "").strip()
        public_key = str(raw or "").strip()
        if key_id and public_key and len(key_id) <= 64 and len(public_key) <= 256:
            result[key_id] = public_key
    return result


def load_gateway_settings(config_path: Path, *, auth_dir: Path) -> GatewaySyncSettings:
    try:
        root = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        root = {}
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    declared = gateway.get("declared_policy") if isinstance(gateway.get("declared_policy"), dict) else {}
    target_value = str(declared.get("target_file") or "").strip()
    token_file = auth_dir / ".gateway_device_token"
    return GatewaySyncSettings(
        enabled=bool(gateway.get("enabled", False)),
        url=str(gateway.get("url") or "").rstrip("/"),
        verify_tls=bool(gateway.get("verify_tls", True)),
        batch_size=max(1, min(int(gateway.get("batch_size", 100)), 500)),
        poll_seconds=max(0.25, float(gateway.get("poll_seconds", 2))),
        policy_refresh_seconds=max(5.0, float(gateway.get("policy_refresh_seconds", 60))),
        local_policy=dict(gateway.get("local_policy") or {}),
        token_file=token_file,
        managed_declared_policy_enabled=bool(declared.get("enabled", False)),
        managed_declared_policy_organization_id=str(declared.get("organization_id") or "").strip(),
        managed_declared_policy_trusted_keys=_trusted_keys(declared.get("trusted_keys")),
        managed_declared_policy_refresh_seconds=max(5.0, float(declared.get("refresh_seconds", 60))),
        managed_declared_policy_target_file=Path(target_value).expanduser() if target_value else None,
    )


def load_device_token(settings: GatewaySyncSettings) -> str:
    env_value = os.getenv("OWG_GATEWAY_DEVICE_TOKEN", "").strip()
    if env_value:
        return env_value
    try:
        return settings.token_file.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def store_device_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(str(token).strip() + "\n", encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
