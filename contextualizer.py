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


def _agent_context_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    if str(meta.get("source") or "") != "agent" and str(meta.get("actor_kind") or "") != "agent":
        return {}
    out: dict[str, Any] = {"actor_kind": "agent"}
    for key in ("operation", "status", "observation_level"):
        value = _clean(meta.get(key), 120)
        if value:
            out[key] = value
    for container_name, allowed in (
        ("agent", ("name", "provider", "framework", "model")),
        ("trace", ("run_id", "trace_id", "span_id", "parent_span_id", "workflow_id", "trigger_event_id")),
        ("tool", ("name", "category")),
    ):
        source = meta.get(container_name) if isinstance(meta.get(container_name), dict) else {}
        safe = {key: _clean(source.get(key), 240) for key in allowed}
        out[container_name] = {key: value for key, value in safe.items() if value}
    usage_raw = meta.get("usage") if isinstance(meta.get("usage"), dict) else {}
    usage: dict[str, int] = {}
    for key in ("input_tokens", "output_tokens", "cached_input_tokens", "total_tokens"):
        try:
            value = int(usage_raw.get(key))
        except Exception:
            continue
        if 0 <= value <= 1_000_000_000:
            usage[key] = value
    if usage:
        out["usage"] = usage
    return out


def contextualize_event(event: dict[str, Any]) -> dict[str, Any]:
    e = dict(event)
    meta = dict(e.get("metadata") or {})
    page = dict(meta.get("page") or {}) if isinstance(meta.get("page"), dict) else {}
    target = dict(meta.get("target") or {}) if isinstance(meta.get("target"), dict) else {}
    agent_context = _agent_context_metadata(meta)

    app = _clean(e.get("app"), 160) or "Unknown"
    title = _clean(page.get("title") or e.get("window_title"), 500)
    hostname = _clean(page.get("hostname"), 240).lower()
    pathname = _clean(page.get("pathname"), 500)
    surface = safe_surface(app=app, hostname=hostname, pathname=pathname, title=title)
    action = _clean(agent_context.get("operation") or meta.get("action") or e.get("event_type"), 120)
    target_label = _safe_target_label(target)
    if agent_context:
        tool = agent_context.get("tool") if isinstance(agent_context.get("tool"), dict) else {}
        target_label = _clean(tool.get("name"), 200)

    resource_locator = ""
    if hostname:
        resource_locator = hostname + (pathname or "/")

    parts = [surface, action, title, resource_locator, target_label]
    context_text = " | ".join(x for x in parts if x)

    context_metadata: dict[str, Any] = {
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
    }
    if agent_context:
        context_metadata.update(agent_context)
        context_metadata["privacy"].update({
            "prompt_content_captured": False,
            "model_response_content_captured": False,
            "tool_arguments_captured": False,
            "tool_result_content_captured": False,
            "chain_of_thought_captured": False,
        })

    event_type = str(e.get("event_type") or "")
    if event_type.startswith("clipboard_"):
        for key in (
            "clipboard_transfer_id",
            "linked_copy_event_id",
            "clipboard_link_age_seconds",
            "clipboard_contents_captured",
        ):
            if key in meta:
                context_metadata[key] = meta[key]

    if event_type == "browser_performance_timing":
        for key in ("response_wait_ms", "dom_ready_ms", "load_complete_ms", "rounded_to_ms"):
            try:
                if key in meta:
                    context_metadata[key] = max(0, min(float(meta[key]), 10 * 60 * 1000))
            except Exception:
                pass
        context_metadata["resource_urls_captured"] = False
        context_metadata["page_contents_captured"] = False

    if event_type == "browser_file_upload_category":
        categories = meta.get("categories") if isinstance(meta.get("categories"), dict) else {}
        safe_categories = {}
        allowed = {"image", "video", "audio", "archive", "spreadsheet", "structured-data", "document", "other", "unknown"}
        for key, value in categories.items():
            if str(key) not in allowed:
                continue
            try:
                safe_categories[str(key)] = max(0, min(int(value), 1000))
            except Exception:
                continue
        context_metadata["categories"] = safe_categories
        try:
            context_metadata["file_count"] = max(0, min(int(meta.get("file_count") or 0), 1000))
        except Exception:
            context_metadata["file_count"] = 0
        context_metadata.update({
            "filename_captured": False,
            "path_captured": False,
            "exact_size_captured": False,
            "file_contents_captured": False,
        })

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
        "metadata": context_metadata,
    }
