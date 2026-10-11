"""Bounded provenance for the separate opt-in text plane; never page contents."""
from __future__ import annotations

import re
from typing import Any


def bounded_context(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}
    for key, pattern in (("page_ref", r"owg:wp:[0-9a-f]{32}"),
                         ("resource_ref", r"owg:r:[0-9a-f]{32}")):
        if isinstance(raw.get(key), str) and re.fullmatch(pattern, raw[key]):
            out[key] = raw[key]
    if raw.get("selection") in {"viewport_then_page", "editable_draft", "legacy_unknown"}:
        out["selection"] = raw["selection"]
    for key in ("capture_truncated", "sync_truncated", "presentation_truncated"):
        if isinstance(raw.get(key), bool):
            out[key] = raw[key]
    for key in ("inspected_characters", "captured_characters", "synced_characters"):
        if isinstance(raw.get(key), int) and not isinstance(raw[key], bool):
            out[key] = max(0, min(1_000_000, raw[key]))
    return out
