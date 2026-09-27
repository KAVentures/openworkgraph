from __future__ import annotations

"""One-string organization join codes.

The code contains only the Gateway URL and a revocable, expiring enrollment
bearer. Organization identity and sharing policy are always resolved from the
Gateway during preview; they are deliberately not trusted from the code itself.
"""

import base64
import json
import re
from typing import Any
from urllib.parse import urlparse

PREFIX = "owgjoin1."
_MAX_CODE_CHARS = 4096
_TOKEN_RE = re.compile(r"^owg_enroll_link_[A-Za-z0-9._~-]{16,400}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _validate_gateway_url(value: str) -> tuple[str, bool]:
    gateway_url = str(value or "").strip().rstrip("/")
    parsed = urlparse(gateway_url)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("join code has an invalid Gateway URL")
    if parsed.username or parsed.password:
        raise ValueError("join code Gateway URL must not contain credentials")
    local = parsed.hostname.casefold() in _LOCAL_HOSTS
    if parsed.scheme == "http" and not local:
        raise ValueError("join code Gateway URL must use https")
    return gateway_url, local


def encode_join_code(*, gateway_url: str, token: str) -> str:
    url, _local = _validate_gateway_url(gateway_url)
    bearer = str(token or "").strip()
    if not _TOKEN_RE.fullmatch(bearer):
        raise ValueError("join code has an invalid enrollment token")
    payload = {"v": 1, "g": url, "t": bearer}
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise ValueError("unsupported join code version")
    gateway_url, local = _validate_gateway_url(str(payload.get("g") or ""))
    token = str(payload.get("t") or "").strip()
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("join code has an invalid enrollment token")
    return {
        "gateway_url": gateway_url,
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
