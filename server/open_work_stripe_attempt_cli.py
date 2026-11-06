"""Explicit, read-only operator verification of one Stripe agent attempt.

Run with python -m server.open_work_stripe_attempt_cli. This is not an agent
execution API, business-outcome receipt, or substitute for a reviewed grant.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .open_work_provider_attempt_verification import _valid, verify_provider_attempt
from .open_work_stripe_attempt_events import read_stripe_attempt_events


def verify_explicit_attempt(*, account: str, object_id: str, operation: str,
                            correlation_id: str, event_id: str, token: str,
                            transport=None) -> dict:
    """Fetch independent provider evidence, then correlate exact identities."""
    if not all(_valid(v) for v in (account, object_id, operation, correlation_id, event_id)):
        return {"status": "unknown", "provider_operation": "unknown",
                "customer_outcome": "unknown", "reason": "invalid_identity"}
    if not isinstance(token, str) or not token.startswith(("rk_test_", "rk_live_", "sk_test_", "sk_live_")):
        return {"status": "unknown", "provider_operation": "unknown",
                "customer_outcome": "unknown", "reason": "missing_provider_credential"}
    return verify_provider_attempt(
        account=account, object_id=object_id, operation=operation,
        correlation_id=correlation_id, expected_event_id=event_id,
        read_events=lambda: read_stripe_attempt_events(
            token=token, expected_account=account, object_id=object_id,
            event_type=operation, correlation_id=correlation_id,
            transport=transport),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Explicit read-only Stripe event/attempt check")
    parser.add_argument("--account", required=True)
    parser.add_argument("--object-id", required=True)
    parser.add_argument("--event-type", required=True)
    parser.add_argument("--idempotency-key", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--confirm-provider-read", action="store_true")
    args = parser.parse_args(argv)
    unknown = {"status": "unknown", "provider_operation": "unknown",
               "customer_outcome": "unknown"}
    if not args.confirm_provider_read:
        result = {**unknown, "reason": "explicit_provider_read_not_confirmed"}
    else:
        result = verify_explicit_attempt(
            account=args.account, object_id=args.object_id,
            operation=args.event_type, correlation_id=args.idempotency_key,
            event_id=args.event_id, token=os.environ.get("OWG_STRIPE_ATTEMPT_READ_KEY", ""))
    # No provider payloads, credential values, or customer data are printed.
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "verified_provider_event" else 3


if __name__ == "__main__":
    sys.exit(main())
