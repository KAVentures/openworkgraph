from __future__ import annotations

import hashlib
import re
from typing import Any

from normalizer import safe_action_label


_STRUCTURAL_ACTIONS = {
    "click": "Click",
    "right click": "Right click",
    "focus control": "Focus control",
    "control change": "Update control",
    "copy": "Copy",
    "paste": "Paste",
    "scroll": "Scroll",
    "form submit": "Submit",
    "page view": "Page view",
    "tab activated": "Tab activated",
    "navigation": "Navigation",
}


def _clean_action(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip()).replace("_", " ")[:240]


def safe_dashboard_action(*values: Any, event_type: str = "") -> str:
    """Return a useful action without echoing arbitrary visible UI text.

    Canonical semantic verbs are intentionally preferred over redacting pieces of
    arbitrary labels. For example, ``Open email from Anna Svensson`` becomes
    ``Open email`` and ``Send to anna@example.com`` becomes ``Send``. Unknown
    labels fall back to a structural event action rather than crossing the human
    dashboard boundary verbatim.
    """
    for raw in values:
        text = _clean_action(raw)
        if not text:
            continue
        # Interaction strings are often formatted "click: Send (button)"; try the
        # inner UI label as well as the full string.
        inner = re.sub(r"^[a-z][a-z ]{0,30}:\s*", "", text, flags=re.IGNORECASE)
        inner = re.sub(r"\s*\([a-z ]{1,30}\)$", "", inner, flags=re.IGNORECASE)
        for candidate in (text, inner) if inner != text else (text,):
            label = safe_action_label({"label": candidate})
            if label:
                return label

    structural = _clean_action(event_type)
    for prefix in ("browser ", "screen ", "clipboard "):
        if structural.lower().startswith(prefix):
            structural = structural[len(prefix):]
            break
    if not structural:
        for raw in values:
            candidate = _clean_action(raw).lower()
            if candidate in _STRUCTURAL_ACTIONS:
                structural = candidate
                break
    key = structural.lower()
    return _STRUCTURAL_ACTIONS.get(key, "Observed action" if (structural or any(values)) else "")


def safe_collector_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not status:
        return None
    return {
        "connected": True,
        "received_at": status.get("received_at"),
    }


def safe_browser_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if not status:
        return None
    return {
        "status": status.get("status"),
        "received_at": status.get("received_at"),
        "sensor_version": status.get("sensor_version"),
        "expected_sensor_version": status.get("expected_sensor_version"),
        "version_ok": status.get("version_ok"),
        "excluded": bool(status.get("excluded")),
    }


def _safe_pattern_step(step: dict[str, Any]) -> dict[str, Any]:
    out = dict(step)
    out["surface"] = str(step.get("surface") or "Unknown")
    out["action"] = safe_dashboard_action(step.get("action"))
    return out


def _safe_pattern_run(run: dict[str, Any]) -> dict[str, Any]:
    out = dict(run)
    out["semantic_actions"] = [
        label
        for raw in (run.get("semantic_actions") or [])
        if (label := safe_dashboard_action(raw))
    ]
    if "action_skeleton" in out:
        out["action_skeleton"] = [
            label
            for raw in (run.get("action_skeleton") or [])
            if (label := safe_dashboard_action(raw))
        ]
    if "steps" in out:
        out["steps"] = [
            _safe_pattern_step(dict(step))
            for step in (run.get("steps") or [])
            if isinstance(step, dict)
        ]
    if "ends_with" in out:
        out["ends_with"] = safe_dashboard_action(run.get("ends_with"))
    # Outcomes can contain arbitrary user/resource text. The dashboard only needs
    # canonical action hints; rich outcomes remain in the local /v1/patterns API.
    if "outcomes" in out:
        out["outcomes"] = [
            label
            for raw in (run.get("outcomes") or [])
            if isinstance(raw, str) and (label := safe_dashboard_action(raw))
        ]
    return out


def dashboard_safe_pattern(pattern: dict[str, Any]) -> dict[str, Any]:
    """Project one repeated-workflow record onto the human dashboard boundary."""
    out = dict(pattern)
    surfaces = [str(value) for value in (pattern.get("surfaces") or []) if str(value or "").strip()]
    safe_actions = [
        label
        for raw in (pattern.get("action_skeleton") or [])
        if (label := safe_dashboard_action(raw))
    ]
    steps = [
        _safe_pattern_step(dict(step))
        for step in (pattern.get("steps") or [])
        if isinstance(step, dict)
    ]
    if steps:
        safe_actions = [str(step.get("action") or "") for step in steps if step.get("action")]

    terminal = safe_dashboard_action(pattern.get("ends_with"), *(safe_actions[-1:] or []))
    if len(surfaces) > 1:
        display_name = " → ".join(surfaces)
    elif surfaces and terminal:
        display_name = f"{surfaces[0]} · {terminal}"
    elif surfaces:
        display_name = surfaces[0]
    elif terminal:
        display_name = terminal
    else:
        display_name = "Repeated workflow"

    rich_identity = "|".join(
        str(pattern.get(key) or "") for key in ("signature", "task_family", "name", "suggested_label")
    )
    pattern_id = "dashboard-" + hashlib.sha256(rich_identity.encode("utf-8", errors="ignore")).hexdigest()[:16]

    out["pattern_id"] = pattern_id
    out["signature"] = pattern_id
    out["task_family"] = pattern_id
    out["suggested_label"] = display_name
    out["name"] = display_name
    out["surfaces"] = surfaces
    out["action_skeleton"] = safe_actions
    out["steps"] = steps
    out["ends_with"] = terminal
    if "runs" in out:
        out["runs"] = [
            _safe_pattern_run(dict(run))
            for run in (pattern.get("runs") or [])
            if isinstance(run, dict)
        ]
    out["dashboard_data_layer"] = "content_minimized"
    return out


def dashboard_safe_patterns(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["patterns"] = [
        dashboard_safe_pattern(dict(pattern))
        for pattern in (payload.get("patterns") or [])
        if isinstance(pattern, dict)
    ]
    out["dashboard_data_layer"] = "content_minimized"
    return out


def dashboard_safe_summary(payload: dict[str, Any]) -> dict[str, Any]:
    out = dict(payload)
    out["repeated_task_patterns"] = [
        dashboard_safe_pattern(dict(pattern))
        for pattern in (payload.get("repeated_task_patterns") or [])
        if isinstance(pattern, dict)
    ]
    return out


def dashboard_safe_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove rich context echoes that the human localhost dashboard does not need."""
    out = dict(profile)
    out["navigation_hunting_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator"}}
        for item in (profile.get("navigation_hunting_candidates") or [])
    ]
    out["rapid_click_candidates"] = [
        {k: v for k, v in dict(item).items() if k not in {"resource_locator", "target_label"}}
        for item in (profile.get("rapid_click_candidates") or [])
    ]
    out["dashboard_data_layer"] = "content_minimized"
    return out


__all__ = [
    "safe_dashboard_action",
    "safe_collector_status",
    "safe_browser_status",
    "dashboard_safe_pattern",
    "dashboard_safe_patterns",
    "dashboard_safe_summary",
    "dashboard_safe_profile",
]
