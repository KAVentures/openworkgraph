"""Sign run reports for an OWG report channel (#370 R18).

The automation keeps the channel secret it was shown once in the dashboard. Each
POST carries three headers, and OWG checks them before storing anything:

* ``X-OWG-Report-Timestamp``: Unix seconds; at most five minutes from OWG's clock.
* ``X-OWG-Report-Sequence``: 1, 2, 3 … per channel; never reused for other bytes.
* ``X-OWG-Report-Signature``: ``v1=`` + hex HMAC-SHA256 of ``"{timestamp}.{sequence}."``
  followed by the exact request body.

Standard library only, so it can be copied into any Python runtime.
"""
from __future__ import annotations

import hashlib
import hmac
import time

SCHEME = "v1"
MAX_SKEW_SECONDS = 300


def signature(secret_hex: str, timestamp: int, sequence: int, body: bytes) -> str:
    message = f"{int(timestamp)}.{int(sequence)}.".encode("ascii") + body
    return SCHEME + "=" + hmac.new(bytes.fromhex(secret_hex), message, hashlib.sha256).hexdigest()


def headers(secret_hex: str, sequence: int, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    stamp = int(time.time()) if timestamp is None else int(timestamp)
    return {"X-OWG-Report-Timestamp": str(stamp), "X-OWG-Report-Sequence": str(int(sequence)),
            "X-OWG-Report-Signature": signature(secret_hex, stamp, sequence, body)}


def verify(secret_hex: str, timestamp: str, sequence: str, provided: str, body: bytes, *, now: float | None = None) -> str | None:
    """``None`` when valid, otherwise a short reason code (never secret material)."""
    try:
        stamp, number = int(timestamp), int(sequence)
    except (TypeError, ValueError):
        return "malformed_headers"
    if number < 1 or not isinstance(provided, str) or not provided.startswith(SCHEME + "="):
        return "malformed_headers"
    if abs((time.time() if now is None else now) - stamp) > MAX_SKEW_SECONDS:
        return "stale_timestamp"
    if not hmac.compare_digest(signature(secret_hex, stamp, number, body), provided):
        return "bad_signature"
    return None


__all__ = ["MAX_SKEW_SECONDS", "headers", "signature", "verify"]
