"""Capture coverage manifest and friction candidates from observed browser interactions (#634).

The coverage manifest says which capture modalities were available, off or
unsupported, so reconstruction and agents do not read a missing signal as an
absent behaviour.

Friction candidates are patterns in *observed* events only:

* ``rapid_repeated_activation``: the same control activated 3+ times within 2 seconds;
* ``quick_backtrack``: returning to the previous page within 15 seconds;
* ``hover_menu_not_followed``: dwelling on a menu or menu item with no activation within 5 seconds;
* ``slow_completion``: more than 10 seconds between activating a control and the next page view.

They are candidates for a person to review. Nothing here infers attention,
confusion, effort, intent or performance, and nothing is per-person scoring.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from . import capture_depth

HIGH_FIDELITY_TYPES = ("browser_control_hover_dwell", "browser_drag_start", "browser_drag_drop", "browser_drag_end",
                       "browser_pointer_trajectory", "screen_drag", "screen_pointer_trajectory",
                       "screen_hover_dwell")
MAX_EVENTS = 20000
CLAIMS = {"observed_patterns_only": True, "intent_or_attention_inferred": False, "per_person_scoring": False,
          "candidates_require_review": True}


def _time(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _target_key(event: dict[str, Any]) -> str:
    target = (event.get("metadata") or {}).get("target") or {}
    return "|".join(str(target.get(k) or "") for k in ("tag", "role", "label"))


def _page(event: dict[str, Any]) -> str:
    page = (event.get("metadata") or {}).get("page") or {}
    return f"{page.get('hostname') or ''}{page.get('pathname') or ''}"


def friction_candidates(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Candidates from time-ordered browser events of one tab or session."""
    out: list[dict[str, Any]] = []
    ordered = sorted(((t, e) for e in events if (t := _time(e.get("observed_at"))) is not None), key=lambda p: p[0])
    activations = [(t, e) for t, e in ordered if e.get("event_type") in ("browser_click", "browser_right_click")]
    i = 0
    while i < len(activations):
        t0, e0 = activations[i]
        key = _target_key(e0)
        j = i
        while j + 1 < len(activations) and _target_key(activations[j + 1][1]) == key and \
                (activations[j + 1][0] - t0).total_seconds() <= 2:
            j += 1
        if j - i + 1 >= 3 and key.strip("|"):
            out.append({"kind": "rapid_repeated_activation", "at": t0.isoformat(), "count": j - i + 1,
                        "page": _page(e0), "target": key, "event_ids": [a[1].get("event_id") for a in activations[i:j + 1]]})
            i = j + 1
        else:
            i += 1
    views = [(t, e) for t, e in ordered if e.get("event_type") == "browser_page_view"]
    for (t1, a), (t2, b), (t3, c) in zip(views, views[1:], views[2:]):
        if _page(a) and _page(a) == _page(c) and _page(a) != _page(b) and (t3 - t2).total_seconds() <= 15:
            out.append({"kind": "quick_backtrack", "at": t3.isoformat(), "page": _page(c), "via": _page(b),
                        "seconds": round((t3 - t2).total_seconds(), 1), "event_ids": [b.get("event_id"), c.get("event_id")]})
    for t, e in ordered:
        if e.get("event_type") != "browser_control_hover_dwell":
            continue
        target = (e.get("metadata") or {}).get("target") or {}
        if str(target.get("role") or "") not in ("menu", "menuitem", "menubar") and \
                (e.get("metadata") or {}).get("ended_by") == "activated":
            continue
        if str(target.get("role") or "") not in ("menu", "menuitem", "menubar"):
            continue
        followed = any(e2.get("event_type") in ("browser_click", "browser_right_click") and 0 <= (t2 - t).total_seconds() <= 5
                       for t2, e2 in ordered)
        if not followed and (e.get("metadata") or {}).get("ended_by") != "activated":
            out.append({"kind": "hover_menu_not_followed", "at": t.isoformat(), "page": _page(e), "target": _target_key(e),
                        "event_ids": [e.get("event_id")]})
    for t, e in activations:
        nxt = next(((t2, e2) for t2, e2 in views if t2 > t), None)
        if nxt and 10 < (nxt[0] - t).total_seconds() <= 300:
            out.append({"kind": "slow_completion", "at": t.isoformat(), "page": _page(e), "target": _target_key(e),
                        "seconds": round((nxt[0] - t).total_seconds(), 1),
                        "event_ids": [e.get("event_id"), nxt[1].get("event_id")]})
    return sorted(out, key=lambda c: c["at"])


def _recent(hours: int) -> list[dict[str, Any]]:
    from .db import rows
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    return rows("SELECT * FROM events WHERE event_type LIKE 'browser_%' AND julianday(observed_at) >= julianday(?) "
                "ORDER BY julianday(observed_at) DESC LIMIT ?", (since, MAX_EVENTS))


def friction_report(hours: int = 24) -> dict[str, Any]:
    if not capture_depth.load()["signals"]["friction_candidates"]:
        return {"enabled": False, "candidates": [], **CLAIMS}
    events = _recent(hours)
    sessions: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        meta = e.get("metadata") or {}
        key = str(meta.get("tab_id") if meta.get("tab_id") is not None else e.get("session_id") or "")
        sessions.setdefault(key, []).append(e)
    candidates = [c for group in sessions.values() for c in friction_candidates(group)]
    candidates.sort(key=lambda c: c["at"], reverse=True)
    return {"enabled": True, "hours": hours, "events_considered": len(events),
            "events_truncated": len(events) >= MAX_EVENTS, "candidates": candidates[:200],
            "counts": dict(Counter(c["kind"] for c in candidates)), **CLAIMS}


def coverage_manifest(hours: int = 168) -> dict[str, Any]:
    """Which capture modalities were available, off or unsupported, with counts of high-fidelity events."""
    from . import main as core
    from .db import rows
    depth = capture_depth.load()
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    placeholders = ",".join("?" for _ in HIGH_FIDELITY_TYPES)
    counts = {r["event_type"]: r["n"] for r in rows(
        f"SELECT event_type, count(*) AS n FROM events WHERE event_type IN ({placeholders}) "
        "AND julianday(observed_at) >= julianday(?) GROUP BY event_type", (*HIGH_FIDELITY_TYPES, since))}
    try:
        from .screen_capture_settings import current as screen
        screen_mode = screen().get("mode", "off")
    except Exception:
        screen_mode = "unknown"
    try:
        from .work_text_capture import get_policy
        work_text = bool(get_policy().get("capture_enabled"))
    except Exception:
        work_text = None
    signals = depth["signals"]
    browser_seen = bool(core.BROWSER_STATUS)
    def state(enabled: bool) -> str:
        if not enabled:
            return "off"
        return "on" if browser_seen else "on_but_browser_extension_not_seen"
    return {"format": "openworkgraph.capture-coverage.v1", "window_hours": hours, "capture_profile": depth["profile"],
            "modalities": {
                "browser_structural_events": "on" if browser_seen else "browser_extension_not_seen",
                "browser_hover_dwell": state(signals["hover_dwell"]),
                "browser_drag_drop": state(signals["drag_drop"]),
                "browser_pointer_trajectory": state(signals["pointer_trajectory"]),
                "friction_candidates": "on" if signals["friction_candidates"] else "off",
                "desktop_clicks_and_scrolls": "on" if core.COLLECTOR_STATUS else "desktop_collector_not_seen",
                "desktop_hover_dwell": ("on" if core.COLLECTOR_STATUS else "on_but_desktop_collector_not_seen") if signals["hover_dwell"] else "off",
                "desktop_drag_drop": ("on" if core.COLLECTOR_STATUS else "on_but_desktop_collector_not_seen") if signals["drag_drop"] else "off",
                "desktop_pointer_trajectory": ("on" if core.COLLECTOR_STATUS else "on_but_desktop_collector_not_seen") if signals["pointer_trajectory"] else "off",
                "screenshots": screen_mode,
                "work_text": "on" if work_text else ("off" if work_text is False else "unknown"),
            },
            "high_fidelity_event_counts": {t: counts.get(t, 0) for t in HIGH_FIDELITY_TYPES},
            "parameters": depth["parameters"],
            "missing_is_not_absence": True, "sharing_unchanged_by_capture_depth": True}


__all__ = ["coverage_manifest", "friction_candidates", "friction_report"]
