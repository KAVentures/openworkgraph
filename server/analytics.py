from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse
import re
from typing import Any

from .db import rows
from browser_utils import is_browser_app


def _workflow_label(e: dict[str, Any]) -> str:
    app = e.get("app") or "Unknown"
    title = (e.get("window_title") or "").strip()
    if is_browser_app(app) and title:
        if len(title) > 80:
            title = title[:77] + "…"
        return f"{app} · {title}"
    return app


def _event_rows(limit: int = 10000, since: str | None = None) -> list[dict[str, Any]]:
    if since:
        return rows(
            "SELECT * FROM events WHERE observed_at >= ? ORDER BY observed_at DESC LIMIT ?",
            (since, max(1, min(limit, 100000))),
        )[::-1]
    return rows(
        "SELECT * FROM events ORDER BY observed_at DESC LIMIT ?",
        (max(1, min(limit, 100000)),),
    )[::-1]


def _interaction_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    action = meta.get("action") or e.get("event_type", "interaction").replace("screen_", "")
    target = meta.get("target") or {}
    role = target.get("role") or target.get("subrole") or ""
    label = target.get("title") or target.get("description") or target.get("identifier") or ""
    if label:
        if len(str(label)) > 80:
            label = str(label)[:77] + "…"
        return f"{action}: {label}" + (f" ({role})" if role else "")
    if role:
        return f"{action}: {role}"
    return str(action)


def _browser_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    action = str(meta.get("action") or e.get("event_type", "").replace("browser_", ""))
    page = meta.get("page") or {}
    target = meta.get("target") or {}
    host = str(page.get("hostname") or "")
    label = str(target.get("label") or target.get("name") or "")
    role = str(target.get("role") or target.get("tag") or "")
    if len(label) > 100:
        label = label[:97] + "…"
    action_text = action.replace("_", " ")
    if label:
        return f"{action_text}: {label}" + (f" ({role})" if role else "")
    if host:
        return f"{action_text}: {host}"
    return action_text


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _friendly_browser_surface(hostname: str, pathname: str = "", title: str = "") -> str:
    host = (hostname or "").lower().strip(".")
    path = pathname or ""
    t = title or ""

    if host in {"chatgpt.com", "chat.openai.com"}:
        return "ChatGPT"
    if host.endswith("lovable.dev"):
        return "Lovable"
    if host in {"github.com", "www.github.com"} or host.endswith(".github.com"):
        return "GitHub"
    if host in {"mail.google.com"}:
        return "Gmail"
    if host == "docs.google.com":
        if path.startswith("/spreadsheets"):
            return "Google Sheets"
        if path.startswith("/document"):
            return "Google Docs"
        if path.startswith("/presentation"):
            return "Google Slides"
        if path.startswith("/forms"):
            return "Google Forms"
        return "Google Workspace"
    if host in {"drive.google.com"}:
        return "Google Drive"
    if host.endswith("notion.so") or host == "notion.com" or host.endswith(".notion.com"):
        return "Notion"
    if host.endswith("slack.com"):
        return "Slack"
    if host in {"teams.microsoft.com", "teams.cloud.microsoft"}:
        return "Microsoft Teams"
    if host in {"outlook.office.com", "outlook.office365.com", "outlook.live.com"}:
        return "Outlook"
    if host.endswith("sharepoint.com"):
        return "SharePoint"
    if host in {"office.com", "www.office.com", "microsoft365.com", "www.microsoft365.com"}:
        return "Microsoft 365"
    if host.endswith("figma.com"):
        return "Figma"
    if host.endswith("linear.app"):
        return "Linear"
    if host.endswith("atlassian.net"):
        if "confluence" in t.lower():
            return "Confluence"
        if "jira" in t.lower():
            return "Jira"
        return "Atlassian"
    if host.endswith("salesforce.com") or host.endswith("force.com"):
        return "Salesforce"
    if host.endswith("hubspot.com"):
        return "HubSpot"

    if host.startswith("www."):
        host = host[4:]
    return host or _surface_from_title(t) or "Browser"


def _surface_from_title(title: str) -> str:
    t = (title or "").strip()
    low = t.lower()
    title_markers = [
        ("gmail", "Gmail"),
        ("google docs", "Google Docs"),
        ("google sheets", "Google Sheets"),
        ("google slides", "Google Slides"),
        ("google drive", "Google Drive"),
        ("chatgpt", "ChatGPT"),
        ("lovable", "Lovable"),
        ("github", "GitHub"),
        ("notion", "Notion"),
        ("salesforce", "Salesforce"),
        ("hubspot", "HubSpot"),
        ("figma", "Figma"),
        ("slack", "Slack"),
        ("microsoft teams", "Microsoft Teams"),
        ("outlook", "Outlook"),
        ("sharepoint", "SharePoint"),
    ]
    for marker, label in title_markers:
        if marker in low:
            return label
    t = re.sub(r"^\(\d+\)\s*", "", t)
    t = re.sub(r"\s+[\-–—]\s+(Google Chrome|Chrome|Microsoft Edge|Edge|Firefox|Safari|Brave|Opera|Vivaldi)$", "", t, flags=re.I)
    return t[:90] if t else "Browser"


def _browser_context_by_session(events: list[dict[str, Any]]) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    out: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for e in events:
        if not str(e.get("event_type") or "").startswith("browser_"):
            continue
        ts = _parse_ts(e.get("observed_at"))
        if ts is None:
            continue
        meta = e.get("metadata") or {}
        page = meta.get("page") or {}
        if page.get("hostname"):
            out[str(e.get("session_id") or "")].append((ts, page))
    for values in out.values():
        values.sort(key=lambda x: x[0])
    return out


def _effort_surface(e: dict[str, Any], browser_context: dict[str, list[tuple[float, dict[str, Any]]]]) -> tuple[str, str]:
    app = str(e.get("app") or "Unknown")
    if not is_browser_app(app):
        return app, app

    title = str(e.get("window_title") or "")
    start = _parse_ts(e.get("observed_at"))
    duration = float(e.get("duration_seconds") or 0)
    end = (start + duration) if start is not None else None
    candidates = browser_context.get(str(e.get("session_id") or ""), [])

    page = None
    if start is not None and end is not None:
        inside = [(ts, p) for ts, p in candidates if start - 1.0 <= ts <= end + 0.75]
        if inside:
            title_low = title.lower()
            matched = [x for x in inside if str(x[1].get("title") or "").lower() == title_low and title_low]
            page = (matched[0] if matched else inside[0])[1]
        else:
            prior = [(ts, p) for ts, p in candidates if ts <= start and start - ts <= 8.0]
            if prior:
                page = prior[-1][1]

    if page:
        surface = _friendly_browser_surface(
            str(page.get("hostname") or ""),
            str(page.get("pathname") or ""),
            str(page.get("title") or title),
        )
        return surface, app

    return _surface_from_title(title), app


_COMPLETION_RE = re.compile(
    r"\b(create|created|submit|submitted|send|sent|publish|published|approve|approved|complete|completed|finish|finished|merge|merged|deploy|deployed|invite|invited|upload|uploaded|confirm|confirmed)\b",
    re.I,
)


def _semantic_surface(e: dict[str, Any]) -> str:
    app = str(e.get("app") or "Unknown")
    meta = e.get("metadata") or {}
    if str(e.get("event_type") or "").startswith("browser_"):
        page = meta.get("page") or {}
        return _friendly_browser_surface(
            str(page.get("hostname") or ""),
            str(page.get("pathname") or ""),
            str(page.get("title") or e.get("window_title") or ""),
        )
    if is_browser_app(app):
        return _surface_from_title(str(e.get("window_title") or ""))
    return app


def _target_label(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    target = meta.get("target") or {}
    for key in ("label", "title", "description", "name", "identifier"):
        value = str(target.get(key) or "").strip()
        if value:
            return value[:120]
    return ""


def _semantic_action(e: dict[str, Any]) -> str:
    meta = e.get("metadata") or {}
    return str(meta.get("action") or str(e.get("event_type") or "").replace("browser_", "").replace("screen_", ""))


def _is_completion_event(e: dict[str, Any]) -> bool:
    if not str(e.get("event_type") or "").startswith(("browser_", "screen_")):
        return False
    action = _semantic_action(e).lower()
    label = _target_label(e)
    if action in {"form_submit", "submit"}:
        return True
    return bool(label and _COMPLETION_RE.search(label))


def _candidate_task_label(events: list[dict[str, Any]], primary_surface: str) -> tuple[str, str]:
    semantic = [e for e in events if str(e.get("event_type") or "").startswith(("browser_", "screen_"))]
    completion = [e for e in semantic if _is_completion_event(e)]
    if completion:
        e = completion[-1]
        label = _target_label(e)
        action = _semantic_action(e).replace("_", " ")
        if label:
            return f"{primary_surface} — {label}", "medium"
        return f"{primary_surface} — {action}", "medium"
    for e in reversed(semantic):
        label = _target_label(e)
        if label:
            return f"{primary_surface} — {label}", "low"
    return f"Work in {primary_surface}", "low"


def _task_signature(task: dict[str, Any]) -> str:
    surfaces = task.get("surfaces") or []
    collapsed: list[str] = []
    for surface in surfaces:
        if not collapsed or collapsed[-1] != surface:
            collapsed.append(surface)
    terminal = str(task.get("terminal_action") or "").lower().strip()
    raw = " > ".join(collapsed[:8]) + (f" | {terminal}" if terminal else "")
    return raw or str(task.get("primary_surface") or "Unknown")


def candidate_tasks(
    limit: int = 25000,
    since: str | None = None,
    *,
    gap_seconds: float = 120.0,
    max_task_seconds: float = 1800.0,
) -> dict[str, Any]:
    events = _event_rows(limit, since=since)
    if not events:
        return {"tasks": [], "patterns": [], "inference": {"type": "heuristic", "gap_seconds": gap_seconds, "max_task_seconds": max_task_seconds}}

    browser_context = _browser_context_by_session(events)
    by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        if str(e.get("event_type") or "") == "heartbeat":
            continue
        by_session[str(e.get("session_id") or "")].append(e)

    tasks: list[dict[str, Any]] = []
    for session_id, session_events in by_session.items():
        session_events.sort(key=lambda e: _parse_ts(e.get("observed_at")) or 0)
        current: list[dict[str, Any]] = []
        current_start: float | None = None
        current_end: float | None = None
        boundary_after = False

        def flush() -> None:
            nonlocal current, current_start, current_end, boundary_after
            if not current or current_start is None or current_end is None:
                current, current_start, current_end, boundary_after = [], None, None, False
                return

            focus = [e for e in current if e.get("event_type") == "focus_span"]
            semantic = [e for e in current if str(e.get("event_type") or "").startswith(("browser_", "screen_"))]
            if not focus and not semantic:
                current, current_start, current_end, boundary_after = [], None, None, False
                return

            surface_order: list[str] = []
            surface_seconds: Counter[str] = Counter()
            fg = engaged = idle = active_input = 0.0
            keys = clicks = scrolls = 0
            for e in current:
                if e.get("event_type") == "focus_span":
                    surface, _ = _effort_surface(e, browser_context)
                    dur = float(e.get("duration_seconds") or 0)
                    surface_seconds[surface] += dur
                    if not surface_order or surface_order[-1] != surface:
                        surface_order.append(surface)
                    activity = (e.get("metadata") or {}).get("activity") or {}
                    fg += dur
                    engaged += float(activity.get("engaged_seconds") or 0)
                    idle += float(activity.get("idle_seconds") or 0)
                    active_input += float(activity.get("active_input_seconds") or 0)
                    keys += int(activity.get("keypress_count") or 0)
                    clicks += int(activity.get("click_count") or 0)
                    scrolls += int(activity.get("scroll_count") or 0)
                elif str(e.get("event_type") or "").startswith(("browser_", "screen_")):
                    surface = _semantic_surface(e)
                    if not surface_order or surface_order[-1] != surface:
                        surface_order.append(surface)

            primary = surface_seconds.most_common(1)[0][0] if surface_seconds else (_semantic_surface(semantic[-1]) if semantic else "Unknown")
            label, confidence = _candidate_task_label(current, primary)
            semantic_labels = []
            for e in semantic:
                text = _browser_label(e) if str(e.get("event_type") or "").startswith("browser_") else _interaction_label(e)
                if text and (not semantic_labels or semantic_labels[-1] != text):
                    semantic_labels.append(text)
            completion = [e for e in semantic if _is_completion_event(e)]
            terminal = ""
            if completion:
                ce = completion[-1]
                terminal = _target_label(ce) or _semantic_action(ce).replace("_", " ")

            start_iso = datetime.fromtimestamp(current_start, timezone.utc).isoformat()
            end_iso = datetime.fromtimestamp(current_end, timezone.utc).isoformat()
            task_id = hashlib.sha256(f"{session_id}|{start_iso}|{label}".encode()).hexdigest()[:16]
            task = {
                "task_id": task_id,
                "session_id": session_id,
                "started_at": start_iso,
                "ended_at": end_iso,
                "elapsed_seconds": round(max(0.0, current_end - current_start), 3),
                "foreground_seconds": round(fg, 3),
                "engaged_seconds": round(engaged, 3),
                "idle_seconds": round(idle, 3),
                "active_input_seconds": round(active_input, 3),
                "keypress_count": keys,
                "click_count": clicks,
                "scroll_count": scrolls,
                "primary_surface": primary,
                "surfaces": surface_order,
                "semantic_action_count": len(semantic),
                "semantic_actions": semantic_labels[-20:],
                "terminal_action": terminal,
                "suggested_label": label,
                "confidence": confidence,
                "inference": "heuristic_candidate",
                "needs_review": True,
            }
            task["signature"] = _task_signature(task)
            tasks.append(task)
            current, current_start, current_end, boundary_after = [], None, None, False

        for e in session_events:
            ts = _parse_ts(e.get("observed_at"))
            if ts is None:
                continue
            event_end = ts + (float(e.get("duration_seconds") or 0) if e.get("event_type") == "focus_span" else 0.0)

            should_break = False
            if current and current_end is not None:
                if boundary_after and ts >= current_end - 0.05:
                    should_break = True
                elif ts - current_end > gap_seconds:
                    should_break = True
                elif current_start is not None and ts - current_start > max_task_seconds:
                    should_break = True
            if should_break:
                flush()

            if not current:
                current_start = ts
                current_end = event_end
            current.append(e)
            current_end = max(current_end or event_end, event_end)
            boundary_after = _is_completion_event(e)
        flush()

    tasks.sort(key=lambda t: t["started_at"], reverse=True)

    pattern_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for task in tasks:
        pattern_map[task["signature"]].append(task)
    patterns = []
    for signature, members in pattern_map.items():
        if len(members) < 2:
            continue
        patterns.append({
            "signature": signature,
            "observed_count": len(members),
            "suggested_label": Counter(t["suggested_label"] for t in members).most_common(1)[0][0],
            "surfaces": Counter(tuple(t["surfaces"]) for t in members).most_common(1)[0][0],
            "total_engaged_seconds": round(sum(float(t["engaged_seconds"]) for t in members), 3),
            "median_engaged_seconds": round(sorted(float(t["engaged_seconds"]) for t in members)[len(members)//2], 3),
            "total_foreground_seconds": round(sum(float(t["foreground_seconds"]) for t in members), 3),
            "keypress_count": sum(int(t["keypress_count"]) for t in members),
            "click_count": sum(int(t["click_count"]) for t in members),
            "confidence": "medium" if any(t["confidence"] == "medium" for t in members) else "low",
            "needs_review": True,
        })
    patterns.sort(key=lambda p: (p["observed_count"], p["total_engaged_seconds"]), reverse=True)

    return {
        "tasks": tasks[:200],
        "patterns": patterns[:50],
        "inference": {
            "type": "heuristic_candidate",
            "gap_seconds": gap_seconds,
            "max_task_seconds": max_task_seconds,
            "labels_are_suggestions": True,
        },
    }


def semantic_activity(limit: int = 200, since: str | None = None) -> list[dict[str, Any]]:
    events = _event_rows(max(limit * 6, 500), since=since)
    semantic: list[dict[str, Any]] = []
    for e in events:
        et = str(e.get("event_type") or "")
        if et.startswith("browser_"):
            meta = e.get("metadata") or {}
            semantic.append({
                "observed_at": e.get("observed_at"),
                "session_id": e.get("session_id"),
                "app": e.get("app"),
                "window_title": e.get("window_title"),
                "event_type": et,
                "label": _browser_label(e),
                "page": meta.get("page") or {},
                "target": meta.get("target") or {},
                "source": "browser_extension",
            })
        elif et.startswith("screen_"):
            meta = e.get("metadata") or {}
            semantic.append({
                "observed_at": e.get("observed_at"),
                "session_id": e.get("session_id"),
                "app": e.get("app"),
                "window_title": e.get("window_title"),
                "event_type": et,
                "label": _interaction_label(e),
                "page": {},
                "target": meta.get("target") or {},
                "source": "desktop_accessibility",
            })
    return semantic[-max(1, min(limit, 2000)):][::-1]


def summary(limit: int = 10000, since: str | None = None) -> dict[str, Any]:
    events = _event_rows(limit, since=since)
    focus_events = [e for e in events if e.get("event_type", "focus_span") == "focus_span"]
    interaction_events = [e for e in events if str(e.get("event_type", "")).startswith("screen_")]
    browser_events = [e for e in events if str(e.get("event_type", "")).startswith("browser_")]
    if not events:
        return {
            "events": 0,
            "focus_events": 0,
            "screen_interactions": 0,
            "browser_semantic_events": 0,
            "sessions": 0,
            "apps": [],
            "surfaces": [],
            "transitions": [],
            "frequent_sequences": [],
            "recent_interactions": [],
            "recent_browser_actions": [],
            "semantic_action_counts": [],
            "candidate_task_count": 0,
            "repeated_task_pattern_count": 0,
            "candidate_tasks": [],
            "repeated_task_patterns": [],
            "total_active_seconds": 0.0,
            "total_engaged_seconds": 0.0,
            "total_idle_seconds": 0.0,
            "total_active_input_seconds": 0.0,
            "keypress_count": 0,
            "click_count": 0,
            "scroll_count": 0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    apps = Counter()
    app_seconds = Counter()
    app_engaged = Counter()
    app_idle = Counter()
    app_active_input = Counter()
    app_keys = Counter()
    app_clicks = Counter()
    app_scrolls = Counter()

    surfaces = Counter()
    surface_seconds = Counter()
    surface_engaged = Counter()
    surface_idle = Counter()
    surface_active_input = Counter()
    surface_keys = Counter()
    surface_clicks = Counter()
    surface_scrolls = Counter()
    surface_containers: dict[str, Counter] = defaultdict(Counter)
    browser_context = _browser_context_by_session(events)
    sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in events:
        sessions[e["session_id"]].append(e)
    for e in focus_events:
        if e.get("app"):
            app = e["app"]
            apps[app] += 1
            app_seconds[app] += float(e.get("duration_seconds") or 0)
            activity = (e.get("metadata") or {}).get("activity") or {}
            app_engaged[app] += float(activity.get("engaged_seconds") or 0)
            app_idle[app] += float(activity.get("idle_seconds") or 0)
            app_active_input[app] += float(activity.get("active_input_seconds") or 0)
            app_keys[app] += int(activity.get("keypress_count") or 0)
            app_clicks[app] += int(activity.get("click_count") or 0)
            app_scrolls[app] += int(activity.get("scroll_count") or 0)

            surface, container = _effort_surface(e, browser_context)
            surfaces[surface] += 1
            surface_seconds[surface] += float(e.get("duration_seconds") or 0)
            surface_engaged[surface] += float(activity.get("engaged_seconds") or 0)
            surface_idle[surface] += float(activity.get("idle_seconds") or 0)
            surface_active_input[surface] += float(activity.get("active_input_seconds") or 0)
            surface_keys[surface] += int(activity.get("keypress_count") or 0)
            surface_clicks[surface] += int(activity.get("click_count") or 0)
            surface_scrolls[surface] += int(activity.get("scroll_count") or 0)
            surface_containers[surface][container] += 1

    transitions = Counter()
    sequences = Counter()
    focus_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in focus_events:
        focus_sessions[e["session_id"]].append(e)
    for session_events in focus_sessions.values():
        collapsed: list[str] = []
        for e in session_events:
            step = _workflow_label(e)
            if not collapsed or collapsed[-1] != step:
                collapsed.append(step)
        for a, b in zip(collapsed, collapsed[1:]):
            transitions[(a, b)] += 1
        for n in (3, 4, 5):
            for i in range(0, max(0, len(collapsed) - n + 1)):
                sequences[tuple(collapsed[i:i+n])] += 1

    first = events[0]["observed_at"]
    last = events[-1]["observed_at"]
    recent_interactions = []
    for e in interaction_events[-30:][::-1]:
        meta = e.get("metadata") or {}
        recent_interactions.append({
            "observed_at": e.get("observed_at"),
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "action": meta.get("action") or str(e.get("event_type", "")).replace("screen_", ""),
            "label": _interaction_label(e),
            "target": meta.get("target") or {},
        })

    recent_browser_actions = []
    semantic_counts = Counter()
    for e in browser_events:
        meta = e.get("metadata") or {}
        semantic_counts[str(meta.get("action") or e.get("event_type", "")).replace("browser_", "")] += 1
    for e in browser_events[-40:][::-1]:
        meta = e.get("metadata") or {}
        page = meta.get("page") or {}
        recent_browser_actions.append({
            "observed_at": e.get("observed_at"),
            "app": e.get("app"),
            "window_title": e.get("window_title"),
            "action": meta.get("action") or str(e.get("event_type", "")).replace("browser_", ""),
            "label": _browser_label(e),
            "hostname": page.get("hostname"),
            "pathname": page.get("pathname"),
            "target": meta.get("target") or {},
        })

    task_data = candidate_tasks(limit=limit, since=since)

    return {
        "events": len(events),
        "focus_events": len(focus_events),
        "screen_interactions": len(interaction_events),
        "browser_semantic_events": len(browser_events),
        "sessions": len(sessions),
        "first_observed_at": first,
        "last_observed_at": last,
        "total_active_seconds": round(sum(app_seconds.values()), 3),
        "total_engaged_seconds": round(sum(app_engaged.values()), 3),
        "total_idle_seconds": round(sum(app_idle.values()), 3),
        "total_active_input_seconds": round(sum(app_active_input.values()), 3),
        "keypress_count": int(sum(app_keys.values())),
        "click_count": int(sum(app_clicks.values())),
        "scroll_count": int(sum(app_scrolls.values())),
        "surfaces": [
            {
                "surface": name,
                "container_app": surface_containers[name].most_common(1)[0][0] if surface_containers[name] else name,
                "events": c,
                "active_seconds": round(surface_seconds[name], 3),
                "engaged_seconds": round(surface_engaged[name], 3),
                "idle_seconds": round(surface_idle[name], 3),
                "active_input_seconds": round(surface_active_input[name], 3),
                "keypress_count": int(surface_keys[name]),
                "click_count": int(surface_clicks[name]),
                "scroll_count": int(surface_scrolls[name]),
            }
            for name, c in surfaces.most_common(30)
        ],
        "apps": [
            {
                "app": a, "events": c,
                "active_seconds": round(app_seconds[a], 3),
                "engaged_seconds": round(app_engaged[a], 3),
                "idle_seconds": round(app_idle[a], 3),
                "active_input_seconds": round(app_active_input[a], 3),
                "keypress_count": int(app_keys[a]),
                "click_count": int(app_clicks[a]),
                "scroll_count": int(app_scrolls[a]),
            }
            for a, c in apps.most_common(15)
        ],
        "transitions": [
            {"from": a, "to": b, "count": c}
            for (a, b), c in transitions.most_common(20)
        ],
        "frequent_sequences": [
            {"sequence": list(seq), "count": c}
            for seq, c in sequences.most_common(20)
            if c >= 2
        ],
        "recent_interactions": recent_interactions,
        "recent_browser_actions": recent_browser_actions,
        "semantic_action_counts": [
            {"action": action, "count": count}
            for action, count in semantic_counts.most_common(20)
        ],
        "candidate_task_count": len(task_data.get("tasks", [])),
        "repeated_task_pattern_count": len(task_data.get("patterns", [])),
        "candidate_tasks": task_data.get("tasks", [])[:25],
        "repeated_task_patterns": task_data.get("patterns", [])[:20],
        "task_inference": task_data.get("inference", {}),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def search_events(query: str = "", app: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    clauses = ["1=1"]
    params: list[Any] = []
    if query:
        clauses.append("(LOWER(window_title) LIKE ? OR LOWER(app) LIKE ? OR LOWER(metadata_json) LIKE ?)")
        q = f"%{query.lower()}%"
        params.extend([q, q, q])
    if app:
        clauses.append("LOWER(app) = ?")
        params.append(app.lower())
    params.append(max(1, min(limit, 1000)))
    return rows(
        f"SELECT * FROM events WHERE {' AND '.join(clauses)} ORDER BY observed_at DESC LIMIT ?",
        tuple(params),
    )


def timeline(session_id: str, limit: int = 1000) -> list[dict[str, Any]]:
    return rows(
        "SELECT * FROM events WHERE session_id = ? ORDER BY observed_at ASC LIMIT ?",
        (session_id, max(1, min(limit, 5000))),
    )
