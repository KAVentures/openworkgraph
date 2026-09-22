from __future__ import annotations

import argparse
import os
from pathlib import Path

from .service import enroll_endpoint


def main() -> None:
    parser = argparse.ArgumentParser(description="Enroll this OpenWorkGraph endpoint with a self-hosted Gateway")
    parser.add_argument("--gateway", required=True)
    parser.add_argument("--organization", required=True)
    parser.add_argument("--actor", default="")
    parser.add_argument("--device", default="")
    parser.add_argument("--enrollment-token", default=os.getenv("OWG_GATEWAY_ENROLLMENT_TOKEN", ""))
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--insecure", action="store_true", help="Allow non-HTTPS remote Gateway and disable TLS verification; local development only")
    args = parser.parse_args()

    if not args.enrollment_token:
        raise SystemExit("An enrollment token is required.")

    result = enroll_endpoint(
        Path(args.config).resolve(),
        gateway_url=args.gateway,
        organization_id=args.organization,
        actor_id=args.actor,
        enrollment_token=args.enrollment_token,
        verify_tls=not args.insecure,
        allow_insecure_http=args.insecure,
        device_id=args.device,
    )

    print("OpenWorkGraph endpoint enrolled and connected.")
    print(f"Gateway: {result['gateway_url']}")
    print(f"Organization: {result['organization_id']}")
    print(f"Actor: {result['actor_id'] or '(not assigned)'}")
    print(f"Device: {result['device_id']}")
    print("The enrollment token was not stored. The device credential is stored only in the local OpenWorkGraph auth directory.")
    print("If OpenWorkGraph is already running, use the dashboard instead so sync starts immediately; otherwise start OpenWorkGraph normally.")


if __name__ == "__main__":
    main()
