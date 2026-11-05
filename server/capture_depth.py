"""Capture-depth profiles and per-signal controls for high-fidelity local capture (#634).

How much interaction detail is *recorded on this device* is the user's choice:

* ``standard``: the existing structural signals only (clicks, focus, changes, navigation…);
* ``enhanced``: adds semantic hover dwell on controls, drag and drop, and friction candidates;
* ``full``: adds sampled pointer trajectories (needs its own acknowledgement).

Every signal can also be switched on or off on its own. Capture depth is a separate
decision from retention and sharing: changing it never changes work-text capture,
screenshots, AI access, MCP, Gateway or export settings. The server enforces the
setting at ingest, so an out-of-date or modified extension cannot store a disabled signal.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import tempfile
import threading
from typing import Any

from .db import DATA_DIR

FULL_ACK_VERSION = "capture-depth-full-v1"
FULL_ACKNOWLEDGEMENT = ("Record sampled pointer movement on this computer while I browse. It stays local; "
                        "sharing, AI access and exports keep their own separate settings.")
SIGNALS = ("hover_dwell", "drag_drop", "friction_candidates", "pointer_trajectory")
PROFILES: dict[str, dict[str, bool]] = {
    "standard": {"hover_dwell": False, "drag_drop": False, "friction_candidates": False, "pointer_trajectory": False},
    "enhanced": {"hover_dwell": True, "drag_drop": True, "friction_candidates": True, "pointer_trajectory": False},
    "full": {"hover_dwell": True, "drag_drop": True, "friction_candidates": True, "pointer_trajectory": True},
}
DEFAULT_PARAMETERS = {"hover_dwell_ms": 750, "trajectory_hz": 4, "max_events_per_minute": 60}
PARAMETER_BOUNDS = {"hover_dwell_ms": (300, 5000), "trajectory_hz": (1, 20), "max_events_per_minute": (10, 600)}
# Which stored browser actions each signal produces. Actions not listed are existing structural signals.
SIGNAL_ACTIONS = {"hover_dwell": {"control_hover_dwell"},
                  "drag_drop": {"drag_start", "drag_drop", "drag_end"},
                  "pointer_trajectory": {"pointer_trajectory"}}
MAX_TRAJECTORY_POINTS = 200
_LOCK = threading.RLock()


class CaptureDepthError(ValueError):
    pass


def _path():
    return DATA_DIR / "capture_depth.json"


def _defaults() -> dict[str, Any]:
    return {"profile": "standard", "signals": dict(PROFILES["standard"]), "parameters": dict(DEFAULT_PARAMETERS),
            "full_acknowledged_version": None, "updated_at": None}


def load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        return _defaults()
    out = _defaults()
    signals = data.get("signals") if isinstance(data.get("signals"), dict) else {}
    out["signals"] = {k: signals.get(k) is True for k in SIGNALS}
    params = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
    for name, (low, high) in PARAMETER_BOUNDS.items():
        value = params.get(name)
        out["parameters"][name] = value if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high \
            else DEFAULT_PARAMETERS[name]
    out["full_acknowledged_version"] = data.get("full_acknowledged_version") if \
        data.get("full_acknowledged_version") == FULL_ACK_VERSION else None
    if out["signals"]["pointer_trajectory"] and out["full_acknowledged_version"] is None:
        out["signals"]["pointer_trajectory"] = False
    out["profile"] = profile_for(out["signals"])
    out["updated_at"] = data.get("updated_at")
    return out


def profile_for(signals: dict[str, bool]) -> str:
    for name, values in PROFILES.items():
        if values == {k: bool(signals.get(k)) for k in SIGNALS}:
            return name
    return "custom"


def save(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"profile", "signals", "parameters", "acknowledgement_version"}:
        raise CaptureDepthError("capture depth takes profile, signals, parameters and acknowledgement_version")
    with _LOCK:
        current = load()
        signals = dict(current["signals"])
        if "profile" in payload:
            if payload["profile"] not in PROFILES:
                raise CaptureDepthError("profile is standard, enhanced or full")
            signals = dict(PROFILES[payload["profile"]])
        if "signals" in payload:
            given = payload["signals"]
            if not isinstance(given, dict) or set(given) - set(SIGNALS) or not all(isinstance(v, bool) for v in given.values()):
                raise CaptureDepthError("signals maps " + ", ".join(SIGNALS) + " to true or false")
            signals.update(given)
        params = dict(current["parameters"])
        if "parameters" in payload:
            given = payload["parameters"]
            if not isinstance(given, dict) or set(given) - set(PARAMETER_BOUNDS):
                raise CaptureDepthError("parameters are " + ", ".join(PARAMETER_BOUNDS))
            for name, value in given.items():
                low, high = PARAMETER_BOUNDS[name]
                if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
                    raise CaptureDepthError(f"{name} must be {low} to {high}")
                params[name] = value
        acknowledged = current["full_acknowledged_version"]
        if signals["pointer_trajectory"] and acknowledged is None:
            if payload.get("acknowledgement_version") != FULL_ACK_VERSION:
                raise CaptureDepthError("acknowledge recording pointer movement before turning on pointer trajectories")
            acknowledged = FULL_ACK_VERSION
        data = {"signals": signals, "parameters": params, "full_acknowledged_version": acknowledged,
                "updated_at": datetime.now(timezone.utc).isoformat()}
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".owg-capture-depth-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out)
            os.replace(temp, path)
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
    return public()


def public() -> dict[str, Any]:
    state = load()
    return {**state, "profiles": {k: dict(v) for k, v in PROFILES.items()}, "parameter_bounds": PARAMETER_BOUNDS,
            "full_acknowledgement": FULL_ACKNOWLEDGEMENT, "full_acknowledgement_version": FULL_ACK_VERSION,
            "changes_sharing": False, "independent_settings": ["work_text_capture", "screen_capture_mode",
                                                               "ai_access", "mcp_tools", "gateway_sync", "exports"],
            "never_captured": ["typed_text", "clipboard_contents", "dragged_file_names_or_contents",
                               "dragged_text_or_html", "secure_field_hover", "page_contents"]}


def for_extension() -> dict[str, Any]:
    """The subset the paired extension needs to decide what to emit."""
    state = load()
    return {"signals": state["signals"], "parameters": state["parameters"], "profile": state["profile"]}


def action_allowed(action: str) -> bool:
    signals = load()["signals"]
    for signal, actions in SIGNAL_ACTIONS.items():
        if action in actions:
            return signals[signal]
    return True


def action_allowed_on_excluded(action: str) -> bool:
    return not any(action in actions for actions in SIGNAL_ACTIONS.values())


def bound_metadata(action: str, metadata: dict[str, Any]) -> dict[str, Any] | None:
    """Keep only the expected, bounded shape for the new high-fidelity actions; ``None`` drops the event."""
    if action == "pointer_trajectory":
        points = metadata.get("points")
        if not isinstance(points, list) or not 0 < len(points) <= MAX_TRAJECTORY_POINTS:
            return None
        clean = []
        for p in points:
            if (not isinstance(p, list) or len(p) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                   for v in p)):
                return None
            t, x, y = p
            if not (0 <= t <= 120000 and 0 <= x <= 1 and 0 <= y <= 1):
                return None
            clean.append([int(t), round(float(x), 3), round(float(y), 3)])
        return {"points": clean, "sample_hz": metadata.get("sample_hz") if isinstance(metadata.get("sample_hz"), int) else None,
                "viewport_bucket": str(metadata.get("viewport_bucket") or "")[:16],
                "coordinates": "viewport_fraction", **_frame(metadata)}
    if action == "control_hover_dwell":
        duration = metadata.get("duration_ms")
        if not isinstance(duration, int) or isinstance(duration, bool) or not 0 < duration <= 600000:
            return None
        return {"duration_ms": duration, "ended_by": metadata.get("ended_by") if metadata.get("ended_by") in
                ("pointer_left", "activated", "page_hidden") else "unknown", "threshold_ms": metadata.get("threshold_ms")
                if isinstance(metadata.get("threshold_ms"), int) else None, **_frame(metadata)}
    if action in SIGNAL_ACTIONS["drag_drop"]:
        kinds = metadata.get("payload_kinds")
        kinds = sorted({k for k in kinds if k in ("files", "text", "html", "uri", "element", "other")})[:6] \
            if isinstance(kinds, list) else []
        completion = metadata.get("completion") if metadata.get("completion") in ("complete", "incomplete", "unknown") \
            else "unknown"
        out = {"payload_kinds": kinds, "completion": completion, "drag_id": str(metadata.get("drag_id") or "")[:32],
               **_frame(metadata)}
        if isinstance(metadata.get("source_target"), dict):
            out["source_target"] = {k: str(v)[:160] for k, v in metadata["source_target"].items()
                                    if k in ("tag", "role", "type", "label") and v}
        return out
    return metadata


def _frame(metadata: dict[str, Any]) -> dict[str, Any]:
    return {k: metadata[k] for k in ("top_frame", "frame_kind", "tab_id", "window_id") if k in metadata}


__all__ = ["CaptureDepthError", "FULL_ACK_VERSION", "PROFILES", "SIGNALS", "action_allowed", "action_allowed_on_excluded", "bound_metadata",
           "for_extension", "load", "public", "save"]
