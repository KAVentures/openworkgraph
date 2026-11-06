"""Capture-depth profiles and per-signal controls for high-fidelity local capture (#634).

How much interaction detail is *recorded on this device* is the user's choice:

* ``standard``: the existing structural signals only (clicks, focus, changes, navigation…);
* ``enhanced``: adds semantic hover dwell on controls, drag and drop, and friction candidates;
* ``full``: adds sampled pointer trajectories (needs its own acknowledgement).

Every signal can also be switched on or off on its own. Capture depth is a separate
decision from retention and sharing: changing it never changes work-text capture,
screenshots, AI access, MCP, Gateway or export settings. The server enforces the
setting at ingest, so an out-of-date extension cannot store a disabled signal.

Enhanced and Full detail are time-limited: every choice that turns on a high-fidelity
signal lasts ``duration_days`` (1–90, default 7) and then falls back to Standard on its
own. The Full acknowledgement is cleared whenever pointer movement is turned off or
expires, so turning Full on again asks again.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import tempfile
import threading
from typing import Any

from .db import DATA_DIR

FULL_ACK_VERSION = "capture-depth-full-v2"
FULL_ACKNOWLEDGEMENT = ("Record sampled pointer movement on this computer while I browse, for the period I choose. "
                        "It stays on this computer, including in local backups I make; sharing, AI access and exports "
                        "keep their own separate settings.")
SIGNALS = ("hover_dwell", "drag_drop", "friction_candidates", "pointer_trajectory")
PROFILES: dict[str, dict[str, bool]] = {
    "standard": {"hover_dwell": False, "drag_drop": False, "friction_candidates": False, "pointer_trajectory": False},
    "enhanced": {"hover_dwell": True, "drag_drop": True, "friction_candidates": True, "pointer_trajectory": False},
    "full": {"hover_dwell": True, "drag_drop": True, "friction_candidates": True, "pointer_trajectory": True},
}
DEFAULT_PARAMETERS = {"hover_dwell_ms": 750, "trajectory_hz": 4, "max_events_per_minute": 60, "retention_days": 30}
PARAMETER_BOUNDS = {"hover_dwell_ms": (300, 5000), "trajectory_hz": (1, 20), "max_events_per_minute": (10, 600),
                    "retention_days": (1, 365)}
# Which stored browser actions each signal produces. Actions not listed are existing structural signals.
SIGNAL_ACTIONS = {"hover_dwell": {"control_hover_dwell"},
                  "drag_drop": {"drag_start", "drag_drop", "drag_end"},
                  "pointer_trajectory": {"pointer_trajectory"}}
MAX_TRAJECTORY_POINTS = 200
DEFAULT_DURATION_DAYS = 7
MAX_DURATION_DAYS = 90
# Metadata keys that only high-fidelity events may carry; they are dropped from every other action.
RESERVED_METADATA_KEYS = ("points", "trajectory", "pointer_points", "sample_hz", "viewport_bucket", "coordinates")
# Page/automation accessibility attributes are untrusted text, including role,
# type and even custom tag names. Only known structural values may be retained.
_TARGET_TAGS = frozenset("a button div input label li option select span summary textarea other".split())
_TARGET_ROLES = frozenset("button link tab menuitem menu menubar option checkbox radio switch combobox treeitem region list listitem row cell gridcell textbox searchbox slider".split())
_TARGET_TYPES = frozenset("button checkbox color date datetime-local email file hidden image month number radio range reset search submit tel text time url week secure".split())
_DESKTOP_ROLES = frozenset({"AXButton", "AXLink", "AXCheckBox", "AXRadioButton", "AXMenu", "AXMenuItem", "AXPopUpButton", "AXTab", "AXTextField", "AXTextArea", "AXList", "AXRow", "AXCell", "AXOutline", "AXSlider", "AXComboBox", "AXImage", "AXGroup", "AXStaticText", "Button", "Hyperlink", "Menu", "MenuItem", "CheckBox", "RadioButton", "Tab", "TabItem", "Edit", "List", "ListItem", "DataItem", "TreeItem", "ComboBox", "Slider", "Text", "Pane", "Group", "button", "link", "list item", "list", "menu item", "menu", "tab", "check box", "radio button", "text", "edit", "slider", "combo box"})
# Stored event types of the high-fidelity signals, browser and desktop.
HIGH_FIDELITY_EVENT_TYPES = ("browser_control_hover_dwell", "browser_drag_start", "browser_drag_drop", "browser_drag_end",
                             "browser_pointer_trajectory", "screen_hover_dwell", "screen_drag", "screen_pointer_trajectory")
HEATMAP_GRID = 20
PURGE_INTERVAL_SECONDS = 3600
_LOCK = threading.RLock()
_LAST_PURGE: dict[str, float | None] = {"at": None}


class CaptureDepthError(ValueError):
    pass


def _path():
    return DATA_DIR / "capture_depth.json"


def _defaults() -> dict[str, Any]:
    return {"profile": "standard", "signals": dict(PROFILES["standard"]), "parameters": dict(DEFAULT_PARAMETERS),
            "full_acknowledged_version": None, "updated_at": None, "expires_at": None, "expired": False}


def _parse_time(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


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
    expires = _parse_time(data.get("expires_at"))
    if any(out["signals"].values()):
        if expires is None or expires <= datetime.now(timezone.utc):
            # High-fidelity detail is never open-ended: once its period ends, capture is Standard again.
            out["signals"] = dict(PROFILES["standard"])
            out["full_acknowledged_version"] = None
            out["expired"] = expires is not None
        else:
            out["expires_at"] = expires.isoformat()
    out["profile"] = profile_for(out["signals"])
    out["updated_at"] = data.get("updated_at")
    return out


def profile_for(signals: dict[str, bool]) -> str:
    for name, values in PROFILES.items():
        if values == {k: bool(signals.get(k)) for k in SIGNALS}:
            return name
    return "custom"


def save(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"profile", "signals", "parameters", "acknowledgement_version",
                                                         "duration_days"}:
        raise CaptureDepthError("capture depth takes profile, signals, parameters, duration_days and acknowledgement_version")
    duration = payload.get("duration_days", DEFAULT_DURATION_DAYS)
    if not isinstance(duration, int) or isinstance(duration, bool) or not 1 <= duration <= MAX_DURATION_DAYS:
        raise CaptureDepthError(f"duration_days must be 1 to {MAX_DURATION_DAYS}")
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
        acknowledged = current["full_acknowledged_version"] if signals["pointer_trajectory"] else None
        if signals["pointer_trajectory"] and acknowledged is None:
            if payload.get("acknowledgement_version") != FULL_ACK_VERSION:
                raise CaptureDepthError("acknowledge recording pointer movement before turning on pointer trajectories")
            acknowledged = FULL_ACK_VERSION
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(days=duration)).isoformat() if any(signals.values()) else None
        data = {"signals": signals, "parameters": params, "full_acknowledged_version": acknowledged,
                "updated_at": now.isoformat(), "expires_at": expires, "duration_days": duration if expires else None}
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
            "duration_days_bounds": [1, MAX_DURATION_DAYS], "default_duration_days": DEFAULT_DURATION_DAYS,
            "labels_recorded_for_high_fidelity_events": False,
            "backups_include_raw_pointer_points": True,
            "full_acknowledgement": FULL_ACKNOWLEDGEMENT, "full_acknowledgement_version": FULL_ACK_VERSION,
            "changes_sharing": False, "independent_settings": ["work_text_capture", "screen_capture_mode",
                                                               "ai_access", "mcp_tools", "gateway_sync", "exports"],
            "never_captured": ["typed_text", "clipboard_contents", "labels_on_hover_and_drag", "secure_field_pointer_samples", "dragged_file_names_or_contents",
                               "dragged_text_or_html", "secure_field_hover", "page_contents"]}


def delete_high_fidelity(*, older_than_days: int | None = None) -> dict[str, Any]:
    """Delete stored high-fidelity interaction events (and their local pointer points).

    ``older_than_days=None`` deletes all of them. Structural evidence (clicks, focus,
    navigation) is never touched.
    """
    from datetime import timedelta
    from .db import connect
    placeholders = ",".join("?" for _ in HIGH_FIDELITY_EVENT_TYPES)
    where, params = f"event_type IN ({placeholders})", list(HIGH_FIDELITY_EVENT_TYPES)
    if older_than_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        where += " AND julianday(observed_at) < julianday(?)"
        params.append(cutoff)
    deleted = {}
    with _LOCK, connect() as conn:
        ids = [r[0] for r in conn.execute(f"SELECT event_id FROM events WHERE {where}", params).fetchall()]
        for table in ("context_events", "normalized_events", "events"):
            removed = 0
            for offset in range(0, len(ids), 500):
                chunk = ids[offset:offset + 500]
                removed += conn.execute(f"DELETE FROM {table} WHERE event_id IN ({','.join('?' for _ in chunk)})",
                                        chunk).rowcount or 0
            deleted[table] = removed
        orphaned = conn.execute("DELETE FROM pointer_trajectory_points WHERE event_id NOT IN "
                                "(SELECT event_id FROM events)").rowcount or 0
    return {"deleted_events": deleted["events"], "deleted_derived_rows": deleted["context_events"] + deleted["normalized_events"],
            "orphaned_point_sets_removed": orphaned, "older_than_days": older_than_days,
            "structural_evidence_kept": True}


def apply_retention(force: bool = False) -> dict[str, Any] | None:
    """Apply the high-fidelity retention limit, at most once an hour unless forced."""
    import time
    now = time.monotonic()
    last = _LAST_PURGE["at"]
    if not force and last is not None and now - last < PURGE_INTERVAL_SECONDS:
        return None
    _LAST_PURGE["at"] = now
    try:
        return delete_high_fidelity(older_than_days=load()["parameters"]["retention_days"])
    except Exception:
        return None


def pointer_heatmap(*, hostname: str | None = None, pathname: str | None = None, hours: int = 168) -> dict[str, Any]:
    """A local review aid: where sampled pointer movement fell on a page, as a coarse grid of counts.

    Only counts per cell leave this function, never the points. Pages are listed when no
    page is chosen. This is not an attention, effort or performance measure.
    """
    from datetime import timedelta
    from .db import connect
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with connect() as conn:
        rows = conn.execute("""SELECT e.metadata_json, p.points_json FROM events e
            JOIN pointer_trajectory_points p ON p.event_id = e.event_id
            WHERE e.event_type = 'browser_pointer_trajectory' AND julianday(e.observed_at) >= julianday(?)
            ORDER BY e.observed_at DESC LIMIT 5000""", (since,)).fetchall()
    pages: dict[str, int] = {}
    grid = [[0] * HEATMAP_GRID for _ in range(HEATMAP_GRID)]
    samples = batches = 0
    for meta_json, points_json in rows:
        try:
            meta, points = json.loads(meta_json or "{}"), json.loads(points_json or "[]")
        except ValueError:
            continue
        page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
        host, path = str(page.get("hostname") or ""), str(page.get("pathname") or "")
        pages[host + path] = pages.get(host + path, 0) + len(points)
        if hostname is None or host != hostname or (pathname is not None and path != pathname):
            continue
        batches += 1
        for point in points:
            if isinstance(point, list) and len(point) == 3:
                x = min(HEATMAP_GRID - 1, max(0, int(float(point[1]) * HEATMAP_GRID)))
                y = min(HEATMAP_GRID - 1, max(0, int(float(point[2]) * HEATMAP_GRID)))
                grid[y][x] += 1
                samples += 1
    out: dict[str, Any] = {"hours": hours, "pages": [{"page": k, "samples": v} for k, v in
                                                     sorted(pages.items(), key=lambda p: (-p[1], p[0]))[:50]],
                           "local_review_only": True, "raw_points_returned": False,
                           "interpretation": ("Where sampled pointer movement fell on this page. It is not a measure of "
                                              "attention, effort or performance.")}
    if hostname is not None:
        out.update(page={"hostname": hostname, "pathname": pathname}, grid=grid, grid_size=HEATMAP_GRID,
                   samples=samples, batches=batches, coordinates="viewport_fraction_cells")
    return out


def for_extension() -> dict[str, Any]:
    """The subset the paired extension needs to decide what to emit."""
    state = load()
    return {"signals": state["signals"], "parameters": state["parameters"], "profile": state["profile"],
            "expires_at": state["expires_at"]}


def action_allowed(action: str) -> bool:
    signals = load()["signals"]
    for signal, actions in SIGNAL_ACTIONS.items():
        if action in actions:
            return signals[signal]
    return True


def action_allowed_on_excluded(action: str) -> bool:
    return not any(action in actions for actions in SIGNAL_ACTIONS.values())


def _structural_target(target: dict[str, Any], *, desktop: bool = False) -> dict[str, str]:
    out = {}
    for key, allowed in (("tag", _TARGET_TAGS), ("role", _TARGET_ROLES | (_DESKTOP_ROLES if desktop else frozenset())),
                         ("type", _TARGET_TYPES), ("localized_role", _DESKTOP_ROLES if desktop else frozenset())):
        value = target.get(key)
        if isinstance(value, str) and value in allowed:
            out[key] = value
    return out


def bound_target(action: str, target: dict[str, Any]) -> dict[str, Any]:
    """High-fidelity events keep structural target fields only (tag, role, type), never a label or name."""
    if not any(action in actions for actions in SIGNAL_ACTIONS.values()):
        return target
    # No label at all: aria-label, title, placeholder and label text can all be set from what the user typed.
    return _structural_target(target)


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
        viewport = metadata.get("viewport_bucket")
        return {"points": clean, "sample_hz": metadata.get("sample_hz") if type(metadata.get("sample_hz")) is int else None,
                "viewport_bucket": viewport if isinstance(viewport, str) and re.fullmatch(r"[0-9]{1,5}x[0-9]{1,5}", viewport) else "",
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
        drag_id = metadata.get("drag_id")
        out = {"payload_kinds": kinds, "completion": completion,
               "drag_id": drag_id if isinstance(drag_id, str) and re.fullmatch(r"(?:[a-z0-9]{1,12}|external)", drag_id) else "",
               **_frame(metadata)}
        if isinstance(metadata.get("source_target"), dict):
            source = metadata["source_target"]
            out["source_target"] = _structural_target(source)
        return out
    # Ordinary actions keep their metadata, minus the keys only high-fidelity events may carry.
    return {k: v for k, v in metadata.items() if k not in RESERVED_METADATA_KEYS}


DESKTOP_SIGNAL_TYPES = {"screen_drag": "drag_drop", "screen_pointer_trajectory": "pointer_trajectory",
                        "screen_hover_dwell": "hover_dwell"}


def filter_desktop_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop desktop drags and pointer samples the current capture depth does not allow, or that are malformed."""
    signals = load()["signals"]
    kept = []
    for event in events:
        signal = DESKTOP_SIGNAL_TYPES.get(str(event.get("event_type") or ""))
        if signal is None:
            kept.append(event)
            continue
        meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        if not signals[signal] or meta.get("excluded"):
            continue
        if signal == "hover_dwell":
            duration = meta.get("duration_ms")
            if not isinstance(duration, int) or isinstance(duration, bool) or not 0 < duration <= 600000 \
                    or not isinstance(meta.get("target"), dict) or meta["target"].get("secure"):
                continue
        if signal in ("hover_dwell", "drag_drop"):
            # Like browser hover and drag: structural fields only, never an accessibility name or label.
            for key in ("target", "source_target", "destination_target"):
                if isinstance(meta.get(key), dict):
                    meta = {**meta, key: _structural_target(meta[key], desktop=True)}
            event = {**event, "metadata": meta}
        if signal == "pointer_trajectory":
            points = meta.get("points")
            if (not isinstance(points, list) or not 0 < len(points) <= MAX_TRAJECTORY_POINTS
                    or not all(isinstance(p, list) and len(p) == 3 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                                                            and abs(v) <= 10 ** 6 for v in p) for p in points)):
                continue
        kept.append(event)
    return kept


def _frame(metadata: dict[str, Any]) -> dict[str, Any]:
    out = {}
    if type(metadata.get("top_frame")) is bool:
        out["top_frame"] = metadata["top_frame"]
    if metadata.get("frame_kind") in ("top", "subframe"):
        out["frame_kind"] = metadata["frame_kind"]
    for key in ("tab_id", "window_id"):
        if type(metadata.get(key)) is int and 0 <= metadata[key] <= 2**53 - 1:
            out[key] = metadata[key]
    return out


__all__ = ["CaptureDepthError", "FULL_ACK_VERSION", "HIGH_FIDELITY_EVENT_TYPES", "PROFILES", "SIGNALS", "action_allowed",
           "action_allowed_on_excluded", "apply_retention", "bound_metadata", "bound_target", "delete_high_fidelity",
           "filter_desktop_events", "for_extension", "load", "pointer_heatmap", "public", "save"]
