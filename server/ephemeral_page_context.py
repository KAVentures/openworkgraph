from __future__ import annotations

"""Local-only, user-approved browser selection for a single AI read.

Not a recorder: never writes to events, JSONL, SQLite, queues or Gateway.
The unredacted text exists only during the authenticated loopback request.
An expired or consumed excerpt is discarded, and the AI gate is rechecked
at read time. Browser exclusions are checked independently of the extension.
"""

import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from typing import Any

_LOCK = threading.RLock()
_PENDING: dict[str, Any] | None = None
MAX_CHARS = 4000
TTL_SECONDS = 300
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9][a-z0-9.-]*[a-z0-9]$", re.I)
_BLOCKED_HOSTS = (
    "accounts.google.com", "login.microsoftonline.com", "account.microsoft.com",
    "paypal.com", "stripe.com", "wise.com", "klarna.com",
    "1177.se", "bankid.com", "bankid.se",
)
_BLOCKED_PATH = re.compile(
    r"(?:^|/)(?:auth|oauth|login|signin|sign-in|password|passcode|"
    r"reset|recovery|recover|mfa|2fa|verify|verification|callback|token|"
    r"billing|payment|checkout)(?:/|$)", re.I
)
_BLOCKED_TITLE = re.compile(
    r"\b(?:password|passcode|one.time.code|verification.code|"
    r"bank|patient|medical.record|journal|health.record|"
    r"payment.card|credit.card)\b", re.I
)
_SECRET = re.compile(
    r"(?:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\bsk-[a-zA-Z0-9_-]{18,}\b|"
    r"\b(?:access[_ -]?token|secret[_ -]?key|api[_ -]?key|password)"
    r"\s*[:=]\s*\S{5,})", re.I
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def clear() -> None:
    global _PENDING
    with _LOCK:
        _PENDING = None


def _host_allowed(host: str, url: str, title: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if not _HOST.fullmatch(host) or ".." in host:
        return False
    if host in {"localhost", "127.0.0.1"} or host.endswith(".local"):
        return False
    if any(host == blocked or host.endswith("." + blocked) for blocked in _BLOCKED_HOSTS):
        return False
    parsed = urlsplit(url)
    if parsed.scheme not in {"https", "http"} or (parsed.hostname or "").lower() != host:
        return False
    if _BLOCKED_PATH.search(parsed.path) or _BLOCKED_TITLE.search(title):
        return False

    # Privacy -> Never record must also apply here, not just to event capture.
    from .capture_exclusions import current
    excluded = current()
    if any(host == pattern.lstrip("*.").lower() or host.endswith("." + pattern.lstrip("*.").lower())
           for pattern in excluded["hosts"] if pattern):
        return False
    if any(str(word).casefold() in title.casefold() for word in excluded["title_words"] if word):
        return False
    return True


def store_selected_text(*, hostname: str, page_url: str, title: str, text: str) -> dict[str, Any]:
    """Accept only deliberate browser-popup selections, already authenticated by HMAC."""
    from .ai_access import ai_access_enabled
    if not ai_access_enabled():
        raise PermissionError("Turn on AI access before sharing a selection.")
    if not isinstance(text, str) or not (1 <= len(text.strip()) <= MAX_CHARS):
        raise ValueError("Select between 1 and 4000 characters.")
    if _SECRET.search(text):
        raise ValueError("Selection appears to contain a secret. No text was shared.")
    if not _host_allowed(str(hostname), str(page_url), str(title)):
        raise PermissionError("This site or page is excluded from content sharing.")
    # Process untrusted content *before* retaining anything, independent of the
    # user's Full title/label setting. Redaction is best effort, not a guarantee.
    from .ai_context import redact_contextually
    redacted = redact_contextually({"text": text, "title": title})
    safe_text = str(redacted["text"]).strip()
    safe_title = str(redacted["title"])[:160]
    if not safe_text or _SECRET.search(safe_text):
        raise ValueError("Selection did not pass the local content filter.")
    now = _now()
    global _PENDING
    with _LOCK:
        _PENDING = {
            "text": safe_text[:MAX_CHARS],
            "title": safe_title,
            "hostname": str(hostname).strip().lower(),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=TTL_SECONDS)).isoformat(),
            "_expires": now + timedelta(seconds=TTL_SECONDS),
        }
    return {"status": "ready_for_one_local_ai_read", "expires_in_seconds": TTL_SECONDS}


def consume_for_ai() -> dict[str, Any]:
    """One read, only through the authenticated local AI route; never via Gateway."""
    from .ai_access import ai_access_enabled
    if not ai_access_enabled():
        clear()
        raise PermissionError("AI access is off.")
    global _PENDING
    with _LOCK:
        item = _PENDING
        _PENDING = None
        if not item or _now() >= item["_expires"]:
            return {"status": "no_approved_selection", "available": False}
        return {k: v for k, v in item.items() if not k.startswith("_")} | {
            "status": "approved_selection", "available": True,
            "source": "local_user_selected_browser_text",
            "untrusted_content": True,
            "persistence": "none",
            "gateway_shared": False,
        }


__all__ = ["store_selected_text", "consume_for_ai", "clear", "MAX_CHARS", "TTL_SECONDS"]
