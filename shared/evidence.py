from __future__ import annotations

import json
from typing import Any


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    if isinstance(value, dict):
        return dict(value)
    raw = row.get("metadata_json")
    if isinstance(raw, str):
        try:
            decoded = json.loads(raw or "{}")
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _nested_number(meta: dict[str, Any], key: str) -> float | int:
    candidates: list[dict[str, Any]] = [meta]
    for name in ("activity", "effort", "timing", "input"):
        value = meta.get(name)
        if isinstance(value, dict):
            candidates.append(value)
    for source in candidates:
        value = source.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
    return 0


def rich_evidence_row(row: dict[str, Any], *, include_identity: bool = False) -> dict[str, Any]:
    """Return one AI-facing evidence event without discarding rich metadata.

    The stored evidence event is canonical. Flat fields are convenience indexes
    only; consumers should inspect ``metadata`` whenever they need the complete
    captured context. Local screenshot paths are intentionally never exposed.
    """
    meta = _metadata(row)
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}

    target_label = (
        target.get("label")
        or target.get("title")
        or target.get("description")
        or target.get("help")
        or ""
    )
    target_role = (
        target.get("role")
        or target.get("localized_role")
        or target.get("tag")
        or ""
    )

    result: dict[str, Any] = {
        "event_id": row.get("event_id"),
        "observed_at": row.get("observed_at"),
        "app": row.get("app"),
        "window_title": row.get("window_title"),
        "event_type": row.get("event_type"),
        "duration_seconds": row.get("duration_seconds", 0) or 0,
        "source": row.get("source", ""),
        "session_id": row.get("session_id", ""),
        "metadata": meta,
        "has_local_screenshot": bool(row.get("screenshot_path")),
        # Convenience fields. These never replace the evidence above.
        "action": meta.get("action", ""),
        "target_label": target_label,
        "target_role": target_role,
        "page_host": page.get("hostname", ""),
        "page_path": page.get("pathname", ""),
        "tab_id": meta.get("tab_id"),
        "tab_context_id": meta.get("tab_context_id"),
        "browser_session_id": meta.get("browser_session_id"),
        "semantic_action": meta.get("semantic_action"),
        "semantic_action_confidence": meta.get("semantic_action_confidence"),
        "clipboard_transfer_id": meta.get("clipboard_transfer_id"),
        "linked_copy_event_id": meta.get("linked_copy_event_id"),
        "clipboard_source_tab_context_id": meta.get("clipboard_source_tab_context_id"),
        "clipboard_link_age_seconds": meta.get("clipboard_link_age_seconds"),
        "foreground_seconds": _nested_number(meta, "foreground_seconds"),
        "engaged_seconds": _nested_number(meta, "engaged_seconds"),
        "active_input_seconds": _nested_number(meta, "active_input_seconds"),
        "idle_seconds": _nested_number(meta, "idle_seconds"),
        "keypress_count": _nested_number(meta, "keypress_count"),
        "click_count": _nested_number(meta, "click_count"),
        "scroll_count": _nested_number(meta, "scroll_count"),
    }
    if include_identity:
        result.update({
            "schema_version": row.get("schema_version") or "1.0",
            "organization_id": row.get("organization_id") or "",
            "actor_id": row.get("actor_id") or "",
            "device_id": row.get("device_id") or "",
            "sensor_id": row.get("sensor_id") or "",
        })

    # Avoid noisy null convenience keys while keeping canonical metadata intact.
    for key in (
        "tab_id", "tab_context_id", "browser_session_id", "semantic_action",
        "semantic_action_confidence", "clipboard_transfer_id", "linked_copy_event_id",
        "clipboard_source_tab_context_id", "clipboard_link_age_seconds",
    ):
        if result.get(key) in (None, ""):
            result.pop(key, None)
    return result


RAW_RICH_EVIDENCE_CONTRACT = {
    "canonical": "stored privacy-hardened evidence event",
    "metadata_preserved": True,
    "derived_fields": "convenience indexes only; never ground truth",
    "typed_text_captured": False,
    "clipboard_contents_captured": False,
    "screenshot_bytes_in_api": False,
}
