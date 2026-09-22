from __future__ import annotations

import argparse
import os
from pathlib import Path

import httpx

from collector.identity import load_or_create_identity
from .config import store_device_token


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

    response = httpx.post(
        f"{args.gateway.rstrip('/')}/v1/devices/enroll",
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
    credential_path = auth_dir / ".gateway_device_token"
    store_device_token(credential_path, token)

    print("OpenWorkGraph endpoint enrolled.")
    print(f"Gateway: {args.gateway.rstrip('/')}")
    print(f"Organization: {payload.get('organization_id')}")
    print(f"Actor: {payload.get('actor_id') or '(not assigned)'}")
    print(f"Device: {payload.get('device_id')}")
    print(f"Credential saved: {credential_path}")
    print("Next: set gateway.enabled=true and gateway.url in config.json, then restart OpenWorkGraph.")


if __name__ == "__main__":
    main()
