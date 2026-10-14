"""Conservative never-capture policy for personal messaging surfaces.

Business collaboration (Slack, Teams, workplace Google Chat) is deliberately
not in this denylist. Personal chat page titles/bodies are not workflow evidence
unless a future explicit opt-in policy is designed separately.
"""
from __future__ import annotations

import re
from typing import Any

_PERSONAL_APPS = frozenset({"messages", "imessage", "whatsapp", "signal", "telegram"})
_PERSONAL_HOSTS = frozenset({
    "web.whatsapp.com", "whatsapp.com", "web.telegram.org",
    "web.telegram.com", "messages.google.com", "messenger.com",
    "www.messenger.com", "web.signal.org",
})
_TITLE_CHAT = re.compile(
    r"(?:^|\s[-–—|·:]\s)(?:WhatsApp|Telegram|Signal|iMessage|Messages)(?=\s|$|[-–—|·:])",
    re.IGNORECASE,
)


def personal_chat_surface(*, app: str = "", title: str = "", hostname: str = "") -> bool:
    application = str(app or "").strip().casefold()
    host = str(hostname or "").strip().casefold().removesuffix(".")
    host = host.split(":", 1)[0]
    return (
        application in _PERSONAL_APPS
        or host in _PERSONAL_HOSTS
        or bool(_TITLE_CHAT.search(str(title or "")))
    )


def suppress_personal_chat_evidence(value: Any) -> Any:
    """Strip personal chat evidence from current/legacy AI payloads.

    This safety boundary is applied even to legacy rows recorded before the
    exclusion policy was introduced. Preserve only structural timestamps and
    opaque event IDs, never titles, message bodies, resource URLs or target text.
    """
    if isinstance(value, list):
        return [suppress_personal_chat_evidence(item) for item in value]
    if not isinstance(value, dict):
        return value
    meta = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    hostname = (value.get("page_host") or value.get("browser_hostname")
                or page.get("hostname") or "")
    title = (value.get("window_title") or value.get("display_hint")
             or value.get("resource_title") or "")
    if personal_chat_surface(
        app=str(value.get("app") or ""), title=str(title), hostname=str(hostname),
    ):
        structural = {
            field: value[field]
            for field in ("event_id", "observed_at", "source", "event_type", "duration_seconds")
            if field in value and not isinstance(value[field], (dict, list))
        }
        structural.update(
            app="Excluded",
            window_title="",
            excluded=True,
            excluded_by="personal_chat_default",
        )
        return structural
    return {key: suppress_personal_chat_evidence(child) for key, child in value.items()}


__all__ = ["personal_chat_surface", "suppress_personal_chat_evidence"]
