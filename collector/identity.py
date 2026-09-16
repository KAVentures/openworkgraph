from __future__ import annotations

import json
import socket
import uuid
from pathlib import Path
from typing import Any

LEGACY_PLACEHOLDER_DEVICE_IDS = {"my-laptop", "my-computer", "device"}


def load_or_create_identity(data_dir: Path, config: dict[str, Any]) -> dict[str, str]:
    """Return stable local identity without requiring an account.

    organization_id and actor_id are optional deployment policy values. Device
    and installation IDs persist locally so restarts do not create a new machine.
    Legacy example device IDs are treated as unset so two copied installs do not
    accidentally share the same identity.
    """
    path = Path(data_dir) / "identity.json"
    stored: dict[str, Any] = {}
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stored = {}

    installation_id = str(stored.get("installation_id") or uuid.uuid4())
    configured_device = str(config.get("device_id") or "").strip()
    if configured_device.lower() in LEGACY_PLACEHOLDER_DEVICE_IDS:
        configured_device = ""
    stored_device = str(stored.get("device_id") or "").strip()
    if stored_device.lower() in LEGACY_PLACEHOLDER_DEVICE_IDS:
        stored_device = ""
    device_id = configured_device or stored_device or f"{socket.gethostname()}-{installation_id[:8]}"

    identity = {
        "installation_id": installation_id,
        "device_id": device_id,
        "sensor_id": f"desktop:{installation_id}",
        "organization_id": str(config.get("organization_id") or stored.get("organization_id") or ""),
        "actor_id": str(config.get("actor_id") or stored.get("actor_id") or ""),
    }

    persisted = {
        "installation_id": installation_id,
        "device_id": device_id,
        "organization_id": identity["organization_id"],
        "actor_id": identity["actor_id"],
    }
    try:
        path.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return identity
