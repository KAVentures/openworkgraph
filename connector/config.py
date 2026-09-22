from __future__ import annotations

import json
import os
from dataclasses import dataclass
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


def load_gateway_settings(config_path: Path, *, auth_dir: Path) -> GatewaySyncSettings:
    try:
        root = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        root = {}
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
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
