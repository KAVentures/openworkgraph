"""Never expose identifiable evidence from explicitly marked private windows.

Use browser chrome markers, not a generic occurrence of 'private' in work text.
Also applies on reads so pre-upgrade rows need no blocking startup migration.
"""
from __future__ import annotations

import re
from typing import Any

_BROWSER = re.compile(r"chrome|chromium|safari|firefox|microsoft edge|brave|vivaldi|opera", re.I)
_MARKER = re.compile(
    r"(?:\(\s*(?:incognito|inkognito|inprivate)\s*\)|"
    r"[-–—]\s*(?:inprivate|private browsing|privat surfning))\s*$", re.I,
)


def private_browser_window(app: str = "", title: str = "") -> bool:
    return bool((_BROWSER.search(str(app)) or _BROWSER.search(str(title)))
                and _MARKER.search(str(title)))


def suppress_private_browsing(value: Any) -> Any:
    if isinstance(value, list):
        return [suppress_private_browsing(item) for item in value]
    if not isinstance(value, dict):
        return value
    title = (value.get("window_title") or value.get("page_title") or value.get("context_title")
             or value.get("resource_title") or value.get("display_hint") or "")
    app = (value.get("app") or value.get("container_app") or value.get("app_or_surface")
           or value.get("surface") or value.get("work_surface") or "")
    if private_browser_window(str(app), str(title)):
        # An allowlist prevents new target/file/clipboard/screenshot fields from
        # accidentally bypassing the boundary. Keep structural event linkage.
        safe = {key: value[key] for key in (
            "event_id", "observed_at", "event_type", "source", "session_id",
            "schema_version", "organization_id", "actor_id", "device_id", "sensor_id",
            "duration_seconds", "started_at", "ended_at", "context_id",
        ) if key in value and not isinstance(value[key], (dict, list))}
        safe.update(app="Excluded", window_title="", metadata={
            "excluded": True, "excluded_by": "private_browser_window",
        })
        for field in ("surface", "work_surface", "container_app", "app_or_surface"):
            if field in value:
                safe[field] = "Excluded"
        for field in ("resource_title", "page_title", "context_title", "display_hint"):
            if field in value:
                safe[field] = ""
        return safe
    return {key: suppress_private_browsing(child) for key, child in value.items()}
