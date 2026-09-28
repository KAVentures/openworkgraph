from __future__ import annotations

"""One-string organization join codes: ``owgjoin1.<base64url JSON>``.

A join code bundles what an employee would otherwise type separately (Gateway
URL, organization id and a reusable enrollment-link token). It grants nothing on
its own beyond enrolling a device via a revocable, expiring, use-limited link,
and never grants read access to anyone's evidence.
"""

import base64
import json
import re
from typing import Any
from urllib.parse import urlparse

PREFIX = "owgjoin1."
_MAX_CODE_CHARS = 4096
# Reusable organization links and personal (identity-bound) invitations.
_TOKEN_RE = re.compile(r"^owg_enroll_(?:link|person)_[A-Za-z0-9._~-]{16,400}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def encode_join_code(*, gateway_url: str, organization_id: str, token: str, organization_name: str = "") -> str:
    payload = {
        "v": 1,
        "g": str(gateway_url or "").rstrip("/"),
        "o": str(organization_id or "").strip(),
        "n": str(organization_name or "").strip()[:120],
        "t": str(token or "").strip(),
    }
    decode_payload(payload)  # validate before handing it out
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported join code version")
    gateway_url = str(payload.get("g") or "").rstrip("/")
    organization_id = str(payload.get("o") or "").strip()
    token = str(payload.get("t") or "").strip()
    name = str(payload.get("n") or "").strip()[:120]
    parsed = urlparse(gateway_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("join code has an invalid Gateway URL")
    if parsed.username or parsed.password:
        raise ValueError("join code Gateway URL must not contain credentials")
    local = parsed.hostname.lower() in _LOCAL_HOSTS
    if parsed.scheme == "http" and not local:
        raise ValueError("join code Gateway URL must use https")
    if not organization_id or len(organization_id) > 512:
        raise ValueError("join code has an invalid organization")
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("join code has an invalid enrollment token")
    return {
        "gateway_url": gateway_url,
        "organization_id": organization_id,
        "organization_name": name or organization_id,
        "token": token,
        "local_gateway": local,
    }


def decode_join_code(code: str) -> dict[str, Any]:
    text = re.sub(r"\s+", "", str(code or ""))
    if not text.startswith(PREFIX) or len(text) > _MAX_CODE_CHARS:
        raise ValueError("not an OpenWorkGraph join code")
    body = text[len(PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError("join code is damaged; copy it again") from exc
    return decode_payload(payload)


__all__ = ["PREFIX", "encode_join_code", "decode_join_code", "decode_payload"]
