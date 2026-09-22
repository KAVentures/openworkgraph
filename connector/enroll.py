from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import httpx

from collector.identity import load_or_create_identity
from .config import store_device_token
from .policy import DEFAULT_LOCAL_POLICY


def _persist_config(
    config_path: Path,
    *,
    gateway_url: str,
    verify_tls: bool,
    organization_id: str,
    actor_id: str,
    device_id: str,
) -> None:
    try:
        root = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    except Exception:
        root = {}
    if not isinstance(root, dict):
        root = {}
    gateway = root.get("gateway") if isinstance(root.get("gateway"), dict) else {}
    gateway = dict(gateway)
    gateway.update({
        "enabled": True,
        "url": gateway_url.rstrip("/"),
        "verify_tls": bool(verify_tls),
    })
    gateway.setdefault("batch_size", 100)
    gateway.setdefault("poll_seconds", 2)
    gateway.setdefault("policy_refresh_seconds", 60)
    gateway.setdefault("local_policy", dict(DEFAULT_LOCAL_POLICY))
    root["gateway"] = gateway
    root["organization_id"] = organization_id
    root["actor_id"] = actor_id
    root["device_id"] = device_id

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = config_path.with_name(config_path.name + ".tmp")
    tmp.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, config_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll this OpenWorkGraph endpoint with a self-hosted Gateway")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--actor", default="")
    parser.add_argument("--device", default="")
    parser.add_argument("--enrollment-token", default=os.getenv("OWG_GATEWAY_ENROLLMENT_TOKEN", ""))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification for local development only")
    args = parser.parse_args()

    if not args.enrollment_token:
        raise SystemExit("An enrollment token is required.")

    config_path = Path(args.config).resolve()
    root = config_path.parent
    data_dir = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data" / "live"))
    auth_dir = Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", root / "data" / "auth"))
    identity = load_or_create_identity(data_dir, {"device_id": args.device, "sensor_id": ""})
    device_id = args.device or str(identity.get("device_id") or "")
    if not device_id:
        raise SystemExit("Could not determine a device ID.")

    gateway_url = args.gateway.rstrip("/")
    response = httpx.post(
        f"{gateway_url}/v1/devices/enroll",
        headers={"Authorization": f"Bearer {args.enrollment_token}"},
        json={"organization_id": args.organization, "actor_id": args.actor, "device_id": device_id},
        timeout=15,
        verify=not args.insecure,
    )
    response.raise_for_status()
    payload = response.json()
    token = str(payload.get("token") or "")
    if not token:
        raise SystemExit("Gateway did not return a device token.")

    assigned_org = str(payload.get("organization_id") or args.organization)
    assigned_actor = str(payload.get("actor_id") or args.actor)
    assigned_device = str(payload.get("device_id") or device_id)
    credential_path = auth_dir / ".gateway_device_token"
    store_device_token(credential_path, token)
    _persist_config(
        config_path,
        gateway_url=gateway_url,
        verify_tls=not args.insecure,
        organization_id=assigned_org,
        actor_id=assigned_actor,
        device_id=assigned_device,
    )

    print("OpenWorkGraph endpoint enrolled and connected.")
    print(f"Gateway: {gateway_url}")
    print(f"Organization: {assigned_org}")
    print(f"Actor: {assigned_actor or '(not assigned)'}")
    print(f"Device: {assigned_device}")
    print(f"Credential saved locally: {credential_path}")
    print("Restart OpenWorkGraph. Local capture remains independent; Gateway sharing can be paused without stopping capture.")


if __name__ == "__main__":
    main()
