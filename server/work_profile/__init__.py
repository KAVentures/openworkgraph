from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import os
import statistics
from typing import Any

from server.db import connect

SELF_TAG_CATEGORIES = (
    "Routine admin",
    "Firefighting",
    "Blocked",
    "Deep work",
    "Customer work",
    "Other",
)

PASSIVE_ACTIONS = {
    "", "focus", "focus_span", "page view", "page_view", "tab activated", "tab_activated",
    "navigation", "navigation started", "navigation_started", "navigation requested",
    "navigation_requested", "navigation committed", "navigation_committed", "dom ready", "dom_ready",
    "pageshow", "heartbeat", "scroll", "mouse move", "mouse_move",
}

AI_SURFACES = {
    "chatgpt": "ChatGPT",
    "openai": "ChatGPT",
    "claude": "Claude",
    "copilot": "Copilot",
    "gemini": "Gemini",
    "cursor": "Cursor",
    "perplexity": "Perplexity",
}

COMMUNICATION_SURFACES = ("gmail", "outlook", "mail", "slack", "teams", "discord")
COMMUNICATION_ACTION_WORDS = ("send", "sent", "reply", "post", "message")


def _dt(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _median(values: list[float]) -> float:
    return round(float(statistics.median(values)), 3) if values else 0.0


def _canonical_surface(value: Any) -> str:
    raw = str(value or "Unknown").strip() or "Unknown"
    lowered = raw.lower()
    prefixes = ("google chrome · ", "chrome · ", "microsoft edge · ", "google chrome - ", "chrome - ")
    for prefix in prefixes:
        if lowered.startswith(prefix):
            raw = raw[len(prefix):].strip() or "Unknown"
            lowered = raw.lower()
            break
    mappings = (
        (("mail.google.com", "gmail"), "Gmail"),
        (("google sheets", "docs.google.com/spreadsheets", "sheets.google.com"), "Google Sheets"),
        (("google docs", "docs.google.com/document"), "Google Docs"),
        (("salesforce",), "Salesforce"),
        (("chatgpt", "chat.openai.com", "openai"), "ChatGPT"),
        (("claude",), "Claude"),
        (("copilot",), "Copilot"),
        (("gemini",), "Gemini"),
        (("cursor",), "Cursor"),
        (("slack",), "Slack"),
        (("teams",), "Microsoft Teams"),
        (("outlook",), "Outlook"),
    )
    for needles, name in mappings:
        if any(needle in lowered for needle in needles):
            return name
    return raw


def _scope_since(scope: str, *, now: datetime | None = None) -> str | None:
    if scope == "all":
        return None
    if scope == "current":
        return os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT")
    if scope == "week":
        point = now or datetime.now(timezone.utc)
        return _iso(point - timedelta(days=7))
    raise ValueError("scope must be current, week, or all")


def _ensure_tag_table() -> None:
    with connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_tags (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT NOT NULL,
              started_at TEXT NOT NULL,
              ended_at TEXT NOT NULL,
              category TEXT NOT NULL,
              session_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_self_tags_time ON self_tags(started_at, ended_at)")


def add_self_tag(*, category: str, started_at: str, ended_at: str, session_id: str = "") -> dict[str, Any]:
    _ensure_tag_table()
    category = str(category or "").strip()
    if category not in SELF_TAG_CATEGORIES:
        raise ValueError("unsupported self-tag category")
    start = _dt(started_at)
    end = _dt(ended_at)
    if start is None or end is None or end <= start:
        raise ValueError("self-tag range must have a valid end after start")
    if (end - start).total_seconds() > 12 * 3600:
        raise ValueError("self-tag range cannot exceed 12 hours")
    created = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO self_tags(created_at, started_at, ended_at, category, session_id) VALUES (?, ?, ?, ?, ?)",
            (created, _iso(start), _iso(end), category, str(session_id or "")),
        )
        tag_id = int(cur.lastrowid)
    return {
        "id": tag_id,
        "created_at": created,
        "started_at": _iso(start),
        "ended_at": _iso(end),
        "category": category,
        "session_id": str(session_id or ""),
        "source": "voluntary_user_annotation",
    }


def list_self_tags(*, since: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
    _ensure_tag_table()
    clauses: list[str] = []
    params: list[Any] = []
    if since:
        clauses.append("ended_at >= ?")
        params.append(since)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as conn:
        rows = conn.execute(
            f"SELECT id, created_at, started_at, ended_at, category, session_id FROM self_tags{where} ORDER BY started_at DESC LIMIT ?",
            (*params, max(1, min(int(limit), 5000))),
        ).fetchall()
    return [dict(row) | {"source": "voluntary_user_annotation"} for row in rows]


def _focus_rows(since: str | None) -> list[dict[str, Any]]:
    params: tuple[Any, ...] = ()
    where = "WHERE n.event_type IN ('focus_span', 'focus_period')"
    if since:
        where += " AND n.observed_at >= ?"
        params = (since,)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT n.id, n.event_id, n.observed_at, n.session_id, n.app, n.event_type,
                   n.duration_seconds, n.metadata_json AS event_metadata,
                   c.surface, c.action, c.resource_locator, c.target_label
            FROM normalized_events n
            LEFT JOIN context_events c ON c.event_id = n.event_id
            {where}
            ORDER BY n.observed_at ASC, n.id ASC
            """,
            params,
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            meta = json.loads(item.pop("event_metadata") or "{}")
        except Exception:
            meta = {}
        activity = meta.get("activity") if isinstance(meta.get("activity"), dict) else {}
        duration = max(0.0, float(item.get("duration_seconds") or 0))
        engaged = max(0.0, min(duration, float(activity.get("engaged_seconds", duration) or 0)))
        item["surface"] = _canonical_surface(item.get("surface") or item.get("app") or "Unknown")
        item["foreground_seconds"] = duration
        item["engaged_seconds"] = engaged
        item["keypress_count"] = int(activity.get("keypress_count", 0) or 0)
        item["click_count"] = int(activity.get("click_count", 0) or 0)
        item["scroll_count"] = int(activity.get("scroll_count", 0) or 0)
        result.append(item)
    return result


def _context_rows(since: str | None) -> list[dict[str, Any]]:
    # The work profile describes the person's own work; agent runs are
    # reported separately (Agents tab, get_agent_runs).
    params: tuple[Any, ...] = ()
    where = "WHERE source != 'agent'"
    if since:
        where += " AND observed_at >= ?"
        params = (since,)
    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT id, event_id, observed_at, session_id, source, surface, action,
                   resource_title, resource_locator, target_label, metadata_json
            FROM context_events {where}
            ORDER BY observed_at ASC, id ASC
            """,
            params,
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["surface"] = _canonical_surface(item.get("surface") or "Unknown")
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        result.append(item)
    return result


def _fragmentation(focus: list[dict[str, Any]]) -> dict[str, Any]:
    if not focus:
        return {
            "surface_switches": 0,
            "surface_switches_per_foreground_hour": 0.0,
            "median_focus_span_seconds": 0.0,
            "longest_uninterrupted_surface_seconds": 0.0,
            "longest_uninterrupted_surface": None,
        }
    foreground = sum(float(row["foreground_seconds"]) for row in focus)
    switches = 0
    merged: list[dict[str, Any]] = []
    prior: dict[str, Any] | None = None
    for row in focus:
        start = _dt(row.get("observed_at"))
        if start is None:
            continue
        end = start + timedelta(seconds=float(row.get("foreground_seconds") or 0))
        if prior is not None:
            prev_end = prior["end"]
            gap = (start - prev_end).total_seconds()
            if row["surface"] != prior["surface"] and gap <= 300:
                switches += 1
        if merged and merged[-1]["surface"] == row["surface"] and (start - merged[-1]["end"]).total_seconds() <= 5:
            merged[-1]["end"] = max(merged[-1]["end"], end)
        else:
            merged.append({"surface": row["surface"], "start": start, "end": end})
        prior = {"surface": row["surface"], "end": end}
    longest = max(merged, key=lambda x: (x["end"] - x["start"]).total_seconds()) if merged else None
    return {
        "surface_switches": switches,
        "surface_switches_per_foreground_hour": round(switches / max(foreground / 3600.0, 1e-9), 2) if foreground else 0.0,
        "median_focus_span_seconds": _median([float(row["foreground_seconds"]) for row in focus]),
        "longest_uninterrupted_surface_seconds": round((longest["end"] - longest["start"]).total_seconds(), 3) if longest else 0.0,
        "longest_uninterrupted_surface": longest["surface"] if longest else None,
        "foreground_seconds": round(foreground, 3),
        "engaged_seconds": round(sum(float(row["engaged_seconds"]) for row in focus), 3),
    }


def _transfer_patterns(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in context:
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        transfer_id = str(meta.get("clipboard_transfer_id") or "").strip()
        action = _clipboard_action(row)
        if not transfer_id or action not in {"copy", "cut", "paste"}:
            continue
        slot = by_id.setdefault(transfer_id, {"sources": [], "pastes": []})
        (slot["pastes"] if action == "paste" else slot["sources"]).append(row)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for transfer_id, bundle in by_id.items():
        if not bundle["sources"] or not bundle["pastes"]:
            continue
        source = bundle["sources"][-1]
        for paste in bundle["pastes"]:
            source_surface = _canonical_surface(source.get("surface"))
            destination_surface = _canonical_surface(paste.get("surface"))
            key = (source_surface, destination_surface)
            item = grouped.setdefault(key, {
                "source_surface": source_surface,
                "destination_surface": destination_surface,
                "count": 0,
                "cross_surface": source_surface != destination_surface,
                "example_transfer_ids": [],
                "example_event_ids": [],
            })
            item["count"] += 1
            if len(item["example_transfer_ids"]) < 5:
                item["example_transfer_ids"].append(transfer_id)
            if len(item["example_event_ids"]) < 10:
                item["example_event_ids"].extend([source.get("event_id"), paste.get("event_id")])
                item["example_event_ids"] = [x for x in item["example_event_ids"] if x][:10]
    return sorted(grouped.values(), key=lambda x: (-int(x["count"]), str(x["source_surface"]), str(x["destination_surface"])))


def _transfer_destinations(transfers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in transfers:
        if not item.get("cross_surface"):
            continue
        destination = str(item.get("destination_surface") or "").strip()
        source = str(item.get("source_surface") or "").strip() or "Unknown"
        if not destination:
            continue
        slot = grouped.setdefault(destination, {
            "destination_surface": destination,
            "count": 0,
            "sources": {},
            "example_event_ids": [],
        })
        count = int(item.get("count") or 0)
        slot["count"] += count
        slot["sources"][source] = int(slot["sources"].get(source) or 0) + count
        slot["example_event_ids"].extend(
            str(value) for value in (item.get("example_event_ids") or []) if value
        )

    rows: list[dict[str, Any]] = []
    for destination, slot in grouped.items():
        sources = [
            {"source_surface": source, "count": count}
            for source, count in sorted(
                slot["sources"].items(),
                key=lambda pair: (-int(pair[1]), str(pair[0])),
            )
        ]
        rows.append({
            "destination_surface": destination,
            "count": int(slot["count"]),
            "source_breakdown": sources,
            "example_event_ids": list(dict.fromkeys(slot["example_event_ids"]))[:10],
            "clipboard_contents_captured": False,
            "grouping_basis": "destination_surface",
            "task_identity_inferred": False,
        })
    return sorted(
        rows,
        key=lambda row: (-int(row["count"]), str(row["destination_surface"])),
    )


def _ai_usage(focus: list[dict[str, Any]], transfers: list[dict[str, Any]], context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = {}

    def ai_name(surface: str) -> str | None:
        lowered = surface.lower()
        for needle, name in AI_SURFACES.items():
            if needle in lowered:
                return name
        return None

    for row in focus:
        name = ai_name(str(row.get("surface") or ""))
        if not name:
            continue
        item = usage.setdefault(name, {"surface": name, "foreground_seconds": 0.0, "engaged_seconds": 0.0, "keypress_count": 0, "click_count": 0, "copies": 0, "pastes": 0, "transfers_in": 0, "transfers_out": 0})
        item["foreground_seconds"] += float(row.get("foreground_seconds") or 0)
        item["engaged_seconds"] += float(row.get("engaged_seconds") or 0)
        item["keypress_count"] += int(row.get("keypress_count") or 0)
        item["click_count"] += int(row.get("click_count") or 0)
    for row in context:
        name = ai_name(str(row.get("surface") or ""))
        if not name:
            continue
        action = str(row.get("action") or "").lower()
        item = usage.setdefault(name, {"surface": name, "foreground_seconds": 0.0, "engaged_seconds": 0.0, "keypress_count": 0, "click_count": 0, "copies": 0, "pastes": 0, "transfers_in": 0, "transfers_out": 0})
        if action in {"copy", "cut"}:
            item["copies"] += 1
        elif action == "paste":
            item["pastes"] += 1
    for transfer in transfers:
        source_name = ai_name(str(transfer.get("source_surface") or ""))
        destination_name = ai_name(str(transfer.get("destination_surface") or ""))
        count = int(transfer.get("count") or 0)
        if source_name:
            usage.setdefault(source_name, {"surface": source_name, "foreground_seconds": 0.0, "engaged_seconds": 0.0, "keypress_count": 0, "click_count": 0, "copies": 0, "pastes": 0, "transfers_in": 0, "transfers_out": 0})["transfers_out"] += count
        if destination_name:
            usage.setdefault(destination_name, {"surface": destination_name, "foreground_seconds": 0.0, "engaged_seconds": 0.0, "keypress_count": 0, "click_count": 0, "copies": 0, "pastes": 0, "transfers_in": 0, "transfers_out": 0})["transfers_in"] += count
    for item in usage.values():
        item["foreground_seconds"] = round(float(item["foreground_seconds"]), 3)
        item["engaged_seconds"] = round(float(item["engaged_seconds"]), 3)
    return sorted(usage.values(), key=lambda x: -float(x["engaged_seconds"]))


def _communication_actions(context: list[dict[str, Any]]) -> dict[str, Any]:
    by_surface: Counter[str] = Counter()
    for row in context:
        surface = str(row.get("surface") or "")
        action = str(row.get("action") or "").lower()
        label = str(row.get("target_label") or "").lower()
        if not any(needle in surface.lower() for needle in COMMUNICATION_SURFACES):
            continue
        if any(word in action or word in label for word in COMMUNICATION_ACTION_WORDS):
            by_surface[surface] += 1
    return {
        "total": sum(by_surface.values()),
        "by_surface": [{"surface": surface, "count": count} for surface, count in by_surface.most_common()],
        "interpretation": "Observed communication actions, not a productivity or quality score.",
    }


def _rhythm(focus: list[dict[str, Any]]) -> dict[str, Any]:
    if not focus:
        return {"days": [], "activity_by_local_hour": []}
    days: dict[str, dict[str, Any]] = {}
    hours: dict[int, dict[str, float]] = defaultdict(lambda: {"foreground_seconds": 0.0, "engaged_seconds": 0.0})
    ordered = []
    for row in focus:
        start = _dt(row.get("observed_at"))
        if start is None:
            continue
        duration = float(row.get("foreground_seconds") or 0)
        end = start + timedelta(seconds=duration)
        ordered.append((start, end, row))
        key = start.date().isoformat()
        day = days.setdefault(key, {"date": key, "first_observed_at": start.isoformat(), "last_observed_at": end.isoformat(), "foreground_seconds": 0.0, "engaged_seconds": 0.0, "long_gap_count": 0, "longest_gap_seconds": 0.0, "weekend": start.weekday() >= 5})
        day["first_observed_at"] = min(day["first_observed_at"], start.isoformat())
        day["last_observed_at"] = max(day["last_observed_at"], end.isoformat())
        day["foreground_seconds"] += duration
        day["engaged_seconds"] += float(row.get("engaged_seconds") or 0)
        hours[start.hour]["foreground_seconds"] += duration
        hours[start.hour]["engaged_seconds"] += float(row.get("engaged_seconds") or 0)
    for (prev_start, prev_end, _prev), (start, _end, _row) in zip(ordered, ordered[1:]):
        if start.date() != prev_start.date():
            continue
        gap = max(0.0, (start - prev_end).total_seconds())
        if gap >= 300:
            day = days[start.date().isoformat()]
            day["long_gap_count"] += 1
            day["longest_gap_seconds"] = max(float(day["longest_gap_seconds"]), gap)
    day_rows = []
    for day in sorted(days.values(), key=lambda x: x["date"]):
        day = dict(day)
        day["foreground_seconds"] = round(float(day["foreground_seconds"]), 3)
        day["engaged_seconds"] = round(float(day["engaged_seconds"]), 3)
        day["longest_gap_seconds"] = round(float(day["longest_gap_seconds"]), 3)
        day_rows.append(day)
    hour_rows = [{"hour": hour, "foreground_seconds": round(vals["foreground_seconds"], 3), "engaged_seconds": round(vals["engaged_seconds"], 3)} for hour, vals in sorted(hours.items())]
    return {"days": day_rows, "activity_by_local_hour": hour_rows, "gap_threshold_seconds": 300, "interpretation": "Observed timing and gaps only; gaps are not automatically classified as breaks."}


def _clipboard_action(row: dict[str, Any]) -> str:
    """Canonicalize observed clipboard action names, not clipboard contents."""
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    action = str(row.get("action") or meta.get("event_type") or "").strip().lower()
    for prefix in ("clipboard_", "browser_"):
        if action.startswith(prefix) and action[len(prefix):] in {"copy", "cut", "paste"}:
            return action[len(prefix):]
    return action


def _clipboard_observation_summary(context: list[dict[str, Any]]) -> dict[str, Any]:
    """Report what was actually observed; absent links do not imply no transfers."""
    counts = Counter()
    for row in context:
        action = _clipboard_action(row)
        if action not in {"copy", "cut", "paste"}:
            continue
        counts[action] += 1
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if action == "paste":
            counts["linked_paste" if str(meta.get("clipboard_transfer_id") or "").strip()
                   else "unlinked_paste"] += 1
    return {
        "copy_events_observed": counts["copy"],
        "cut_events_observed": counts["cut"],
        "paste_events_observed": counts["paste"],
        "pastes_with_transfer_id": counts["linked_paste"],
        "pastes_without_transfer_id": counts["unlinked_paste"],
        "scope": "recorded_context_events_only",
        "manual_transfer_count_is_observed_lower_bound": True,
        "zero_does_not_prove_no_real_world_copy_paste": True,
        "clipboard_contents_captured": False,
    }


def _active_entry_action(action: str) -> bool:
    """Only foreground evidence establishes a resource visit.

    Navigation/timing/page-load signals can fire in background tabs. Treat
    them as observations, not evidence the user switched focus.
    """
    raw = str(action or "").strip().lower().replace(" ", "_")
    if raw.startswith("browser_"):
        raw = raw[len("browser_"):]
    return raw in {
        "tab_activated", "focus", "focus_span", "focus_period",
        "click", "dblclick", "double_click", "pointerdown", "mousedown",
        "keydown", "key_press", "keypress", "keyboard_shortcut",
        "input", "change", "submit", "copy", "paste", "cut", "scroll",
        "clipboard_copy", "clipboard_paste", "clipboard_cut",
        "text_input", "text_edit", "form_submit",
    }


def _hunting_candidates(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count active resource-entry episodes, not events and not per-tab lifetimes.

    A switch from tab A to B and back to A is two visits to A even if A never
    navigated. A desktop focus interval without a URL can interrupt the browser
    episode. Duplicate events in the same active context are not new visits.
    This remains a lower-bound heuristic when active focus wasn't observed.
    """
    visits: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    entry_counts: Counter[tuple[str, str]] = Counter()
    last_by_session: dict[str, tuple[str, str, str]] = {}
    for row in context:
        session = str(row.get("session_id") or "")
        surface = str(row.get("surface") or "Unknown")
        locator = str(row.get("resource_locator") or "").strip()
        action = str(row.get("action") or "").casefold().strip()
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        tab = str(meta.get("tab_context_id") or "")

        if not locator:
            # Desktop focus is still an observed surface transition. Ignore
            # anonymous heartbeats or browser sensor noise with no page URL.
            if action in {"focus_span", "focus", "focus_period"} and surface.casefold() not in {
                "", "unknown", "browser", "chrome", "google chrome",
            }:
                last_by_session[session] = (surface, "", "")
            continue
        # Background navigation requests may fire for a tab that was never
        # displayed; only meaningful/committed/activated observations count.
        key = (surface, locator)
        visits[key].append(row)
        if not _active_entry_action(action):
            # Keep observed telemetry in the event count, but never let
            # background navigation, timing or load events cause a switch.
            continue
        signature = (surface, locator, tab)
        if last_by_session.get(session) != signature:
            entry_counts[key] += 1
        last_by_session[session] = signature
    results = []
    for (surface, locator), rows in visits.items():
        entries = entry_counts[(surface, locator)]
        if entries < 2:
            continue
        actions = [_clipboard_action(row) for row in rows]
        meaningful = sum(1 for action in actions if action not in PASSIVE_ACTIONS)
        results.append({
            "surface": surface,
            "resource_locator": locator,
            "visit_count": entries,
            "observed_event_count": len(rows),
            "visit_count_basis": "observed_active_resource_entries_across_tabs_and_desktop_lower_bound",
            "meaningful_action_count": meaningful,
            "candidate_reason": "repeated_resource_visits",
            "needs_review": True,
            "example_event_ids": [row.get("event_id") for row in rows[:8] if row.get("event_id")],
        })
    return sorted(results, key=lambda x: (-int(x["visit_count"]), str(x["surface"])))[:50]


def compute_work_profile(
    *, scope: str = "current", now: datetime | None = None,
    _preloaded_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    since = _scope_since(scope, now=now)
    focus = _focus_rows(since)
    context = _context_rows(since) if _preloaded_context is None else _preloaded_context
    transfers = _transfer_patterns(context)
    transfer_destinations = _transfer_destinations(transfers)
    tags = list_self_tags(since=since)
    profile = {
        "scope": scope,
        "since": since,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_layer": "derived_local_work_profile",
        "fragmentation": _fragmentation(focus),
        "manual_transfer_patterns": transfers,
        "manual_transfer_destinations": transfer_destinations,
        "clipboard_observation_summary": _clipboard_observation_summary(context),
        "manual_transfer_count": sum(int(x.get("count") or 0) for x in transfers),
        "cross_surface_manual_transfer_count": sum(int(x.get("count") or 0) for x in transfers if x.get("cross_surface")),
        "daily_rhythm": _rhythm(focus),
        "ai_tool_usage": _ai_usage(focus, transfers, context),
        "communication_actions": _communication_actions(context),
        "navigation_hunting_candidates": _hunting_candidates(context),
        "self_tags": tags,
        "privacy": {
            "new_sensor_data_required": False,
            "typed_text_captured": False,
            "individual_key_identities_captured": False,
            "clipboard_contents_captured": False,
            "self_tags_are_voluntary": True,
        },
        "interpretation": {
            "not_a_productivity_score": True,
            "derived_metrics_are_regeneratable": True,
            "manual_transfers_use_existing_clipboard_transfer_links": True,
            "manual_transfer_destinations_are_rollups_not_task_identity": True,
            "navigation_hunting_candidates_require_review": True,
            "communication_actions_are_workload_context_not_quality": True,
            "daily_gaps_are_not_assumed_to_be_breaks": True,
        },
    }
    return profile
