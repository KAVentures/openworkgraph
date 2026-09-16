from __future__ import annotations

"""Build a middle context layer from rich local evidence.

The context layer is intentionally different from both raw evidence and the
content-minimized operational layer:

- raw evidence remains the local source of truth;
- operational events are safe structural telemetry for broad analytics;
- context events preserve useful resource/entity wording for customer-authorized
  AI retrieval without ever adding typed field values or clipboard contents.

The browser sensor already strips query strings/fragments. Contextualization also
omits screenshot paths and secure-control labels.
"""

import re
from typing import Any

from normalizer import safe_surface


def _clean(value: Any, limit: int = 500) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_target_label(target: dict[str, Any]) -> str:
    if not target or target.get("secure"):
        return ""
    text = ""
    for key in ("label", "title", "description", "help", "name", "identifier"):
        text = _clean(target.get(key), 240)
        if text:
            break
    if re.search(r"password|passcode|one[- ]?time|\botp\b|security code|credit card", text, re.I):
        return ""
    return text


def contextualize_event(event: dict[str, Any]) -> dict[str, Any]:
    e = dict(event)
    meta = dict(e.get("metadata") or {})
    page = dict(meta.get("page") or {}) if isinstance(meta.get("page"), dict) else {}
    target = dict(meta.get("target") or {}) if isinstance(meta.get("target"), dict) else {}

    app = _clean(e.get("app"), 160) or "Unknown"
    title = _clean(page.get("title") or e.get("window_title"), 500)
    hostname = _clean(page.get("hostname"), 240).lower()
    pathname = _clean(page.get("pathname"), 500)
    surface = safe_surface(app=app, hostname=hostname, pathname=pathname, title=title)
    action = _clean(meta.get("action") or e.get("event_type"), 120)
    target_label = _safe_target_label(target)

    # Query strings/fragments are absent from browser telemetry. Keep hostname +
    # pathname because they are often the only useful key for finding a prior
    # ticket/repository/document workflow. This layer stays customer-controlled.
    resource_locator = ""
    if hostname:
        resource_locator = hostname + (pathname or "/")

    parts = [surface, action, title, resource_locator, target_label]
    context_text = " | ".join(x for x in parts if x)

    return {
        "event_id": str(e.get("event_id") or ""),
        "observed_at": str(e.get("observed_at") or ""),
        "schema_version": str(e.get("schema_version") or "1.0"),
        "organization_id": str(e.get("organization_id") or ""),
        "actor_id": str(e.get("actor_id") or ""),
        "device_id": str(e.get("device_id") or ""),
        "sensor_id": str(e.get("sensor_id") or ""),
        "session_id": str(e.get("session_id") or ""),
        "source": str(e.get("source") or meta.get("source") or "desktop"),
        "surface": surface,
        "action": action,
        "resource_title": title,
        "resource_locator": resource_locator,
        "target_label": target_label,
        "context_text": context_text,
        "metadata": {
            "event_type": str(e.get("event_type") or "unknown"),
            "duration_seconds": float(e.get("duration_seconds") or 0),
            "activity": dict(meta.get("activity") or {}) if isinstance(meta.get("activity"), dict) else {},
            "privacy": {
                "typed_values": False,
                "clipboard_contents": False,
                "url_query": False,
                "url_fragment": False,
                "raw_evidence_separate": True,
            },
        },
    }
