from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import re
from typing import Any

from . import analytics


_AGENTIC_SURFACE_TOKENS = (
    "lovable",
    "chatgpt",
    "claude",
    "cursor",
    "codex",
    "copilot",
    "gemini",
    "perplexity",
    "replit",
    "bolt",
    "jev",
)

_AGENT_SUBMIT_RE = re.compile(
    r"^\s*(send(?:\s+(?:message|follow[- ]?up))?|submit(?:\s+prompt)?|ask|generate|regenerate|run)\b",
    re.I,
)

_OUTCOME_RE = re.compile(
    r"^\s*(publish|published|deploy|deployed|merge|merged|complete|completed|finish|finished|"
    r"submit|submitted|approve|approved|upload|uploaded|confirm|confirmed)\b",
    re.I,
)

_PASSIVE_ACTIONS = {
    "page_view",
    "tab_activated",
    "navigation",
    "navigation_started",
    "navigation_requested",
    "navigation_committed",
    "dom_ready",
    "pageshow",
    "heartbeat",
    "scroll",
    "mouse_move",
}


def _parse_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def _event_id(e: dict[str, Any]) -> str:
    value = str(e.get("event_id") or "").strip()
    if value:
        return value
    raw = "|".join(
        [
            str(e.get("session_id") or ""),
            str(e.get("observed_at") or ""),
            str(e.get("event_type") or ""),
            str(e.get("app") or ""),
        ]
    )
    return "derived-" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _semantic_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in events
        if str(e.get("event_type") or "").startswith(("browser_", "screen_"))
    ]


def _semantic_action(e: dict[str, Any]) -> str:
    return analytics._semantic_action(e)


def _target_label(e: dict[str, Any]) -> str:
    return analytics._target_label(e)


def _semantic_surface(e: dict[str, Any]) -> str:
    return analytics._semantic_surface(e)


def _page(e: dict[str, Any]) -> dict[str, Any]:
    meta = e.get("metadata") or {}
    value = meta.get("page")
    return value if isinstance(value, dict) else {}


def _context_title(e: dict[str, Any]) -> str:
    page = _page(e)
    return str(page.get("title") or e.get("window_title") or "").strip()


def _is_agentic_surface(surface: str, e: dict[str, Any] | None = None) -> bool:
    event = e or {}
    page = _page(event)
    hostname = str(page.get("hostname") or "").strip().lower()
    title = _context_title(event).lower()

    # Treat Vercel v0 specifically rather than matching every unrelated "v0.x" title.
    if hostname == "v0.dev" or hostname.endswith(".v0.dev") or "v0 by vercel" in title:
        return True

    haystack = " ".join(
        [
            str(surface or ""),
            str(event.get("app") or ""),
            title,
            hostname,
        ]
    ).lower()
    return any(token in haystack for token in _AGENTIC_SURFACE_TOKENS)


def _is_agent_submit(e: dict[str, Any]) -> bool:
    surface = _semantic_surface(e)
    if not _is_agentic_surface(surface, e):
        return False
    label = _target_label(e).strip()
    action = _semantic_action(e).strip().replace("_", " ")
    text = label or action
    return bool(text and _AGENT_SUBMIT_RE.search(text))


def _clean_context_name(title: str, surface: str) -> str:
    value = re.sub(r"^\(\d+\)\s*", "", (title or "").strip())
    if not value:
        return ""
    suffixes = [
        surface,
        "Google Chrome",
        "Chrome",
        "Microsoft Edge",
        "Edge",
        "Firefox",
        "Safari",
        "Brave",
    ]
    for suffix in suffixes:
        if not suffix:
            continue
        value = re.sub(
            rf"\s*[|·\-–—]\s*{re.escape(suffix)}\s*$",
            "",
            value,
            flags=re.I,
        ).strip()
    low = value.lower()
    if not value or low in {
        surface.lower(),
        "browser",
        "new tab",
        "google",
        "home",
        "lovable",
        "chatgpt",
        "claude",
    }:
        return ""
    return value[:97] + "…" if len(value) > 100 else value


def _events_by_session(
    events: list[dict[str, Any]],
) -> dict[str, list[tuple[float, dict[str, Any]]]]:
    out: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    for e in events:
        ts = _parse_ts(e.get("observed_at"))
        if ts is None:
            continue
        out[str(e.get("session_id") or "")].append((ts, e))
    for values in out.values():
        values.sort(key=lambda x: x[0])
    return out


def agent_turns(
    limit: int = 100000,
    since: str | None = None,
    *,
    _raw_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Observed agent-submit boundaries kept separate from higher-level task inference."""
    raw_events = (
        list(_raw_events)
        if _raw_events is not None
        else analytics._event_rows(limit, since=since)
    )
    by_session = _events_by_session(_semantic_events(raw_events))
    turns: list[dict[str, Any]] = []

    for session_id, timed in by_session.items():
        submit_indexes = [i for i, (_ts, e) in enumerate(timed) if _is_agent_submit(e)]
        for pos, idx in enumerate(submit_indexes):
            _ts, e = timed[idx]
            surface = _semantic_surface(e)
            next_submit_ts = (
                timed[submit_indexes[pos + 1]][0]
                if pos + 1 < len(submit_indexes)
                else None
            )
            next_user_action_at = None
            next_user_action_id = ""
            for later_ts, later in timed[idx + 1 :]:
                if next_submit_ts is not None and later_ts >= next_submit_ts:
                    break
                action = _semantic_action(later).lower()
                if action in _PASSIVE_ACTIONS:
                    continue
                next_user_action_at = _iso(later_ts)
                next_user_action_id = _event_id(later)
                break

            label = _target_label(e) or _semantic_action(e).replace("_", " ")
            page = _page(e)
            turn_id = hashlib.sha256(
                f"{session_id}|{e.get('observed_at')}|{surface}|{label}".encode()
            ).hexdigest()[:16]
            turns.append(
                {
                    "agent_turn_id": turn_id,
                    "session_id": session_id,
                    "submitted_at": e.get("observed_at"),
                    "surface": surface,
                    "context_title": _context_title(e),
                    "page_host": page.get("hostname", ""),
                    "page_path": page.get("pathname", ""),
                    "trigger_action": label,
                    "turn_kind": (
                        "follow_up"
                        if re.search(r"follow[- ]?up|regenerate|again", label, re.I)
                        else "prompt"
                    ),
                    "evidence_event_id": _event_id(e),
                    "next_agent_turn_at": (
                        _iso(next_submit_ts) if next_submit_ts is not None else None
                    ),
                    "next_user_action_at": next_user_action_at,
                    "next_user_action_event_id": next_user_action_id,
                    "agent_execution_duration_unknown": True,
                    "inference": "observed_agent_turn_boundary_v1",
                }
            )

    turns.sort(key=lambda x: str(x.get("submitted_at") or ""))
    return turns


def factual_context_timeline(
    limit: int = 100000,
    since: str | None = None,
    *,
    _raw_events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compact factual index over immutable raw evidence with source event references."""
    raw_events = (
        list(_raw_events)
        if _raw_events is not None
        else analytics._event_rows(limit, since=since)
    )
    browser_context = analytics._browser_context_by_session(raw_events)

    semantic_by_session: dict[str, list[tuple[float, dict[str, Any]]]] = defaultdict(list)
    semantic_times: dict[str, list[float]] = defaultdict(list)
    for e in _semantic_events(raw_events):
        ts = _parse_ts(e.get("observed_at"))
        if ts is None:
            continue
        sid = str(e.get("session_id") or "")
        semantic_by_session[sid].append((ts, e))
    for sid, values in semantic_by_session.items():
        values.sort(key=lambda x: x[0])
        semantic_times[sid] = [ts for ts, _e in values]

    rows: list[dict[str, Any]] = []
    for e in raw_events:
        if e.get("event_type") != "focus_span":
            continue
        start = _parse_ts(e.get("observed_at"))
        if start is None:
            continue
        duration = max(0.0, float(e.get("duration_seconds") or 0))
        end = start + duration
        sid = str(e.get("session_id") or "")
        surface, container = analytics._effort_surface(e, browser_context)
        activity = (e.get("metadata") or {}).get("activity") or {}

        sem_values = semantic_by_session.get(sid, [])
        sem_times = semantic_times.get(sid, [])
        lo = bisect_left(sem_times, start - 1.0)
        hi = bisect_right(sem_times, end + 0.75)
        nearby = [item for _ts, item in sem_values[lo:hi]]

        semantic_actions: list[str] = []
        evidence_ids = [_event_id(e)]
        page_host = ""
        page_path = ""
        page_title = ""
        for item in nearby:
            evidence_ids.append(_event_id(item))
            text = (
                analytics._browser_label(item)
                if str(item.get("event_type") or "").startswith("browser_")
                else analytics._interaction_label(item)
            )
            if text and (not semantic_actions or semantic_actions[-1] != text):
                semantic_actions.append(text)
            page = _page(item)
            if page.get("hostname") and not page_host:
                page_host = str(page.get("hostname") or "")
                page_path = str(page.get("pathname") or "")
                page_title = str(page.get("title") or "")

        context_id = hashlib.sha256(
            f"{sid}|{e.get('observed_at')}|{_event_id(e)}".encode()
        ).hexdigest()[:16]
        rows.append(
            {
                "context_id": context_id,
                "session_id": sid,
                "started_at": e.get("observed_at"),
                "ended_at": _iso(end),
                "work_surface": surface,
                "container_app": container,
                "window_title": e.get("window_title") or "",
                "page_title": page_title,
                "page_host": page_host,
                "page_path": page_path,
                "foreground_seconds": round(duration, 3),
                "engaged_seconds": round(float(activity.get("engaged_seconds") or 0), 3),
                "idle_seconds": round(float(activity.get("idle_seconds") or 0), 3),
                "active_input_seconds": round(
                    float(activity.get("active_input_seconds") or 0), 3
                ),
                "keypress_count": int(activity.get("keypress_count") or 0),
                "click_count": int(activity.get("click_count") or 0),
                "scroll_count": int(activity.get("scroll_count") or 0),
                "semantic_actions": semantic_actions[:24],
                "evidence_event_ids": evidence_ids[:100],
                "evidence_event_count": len(evidence_ids),
                "evidence_refs_truncated": len(evidence_ids) > 100,
                "inference": "factual_context_index_v1",
            }
        )

    rows.sort(key=lambda x: str(x.get("started_at") or ""))
    return rows


def _task_boundary_is_agent_submit(
    task: dict[str, Any],
    raw_by_session: dict[str, list[tuple[float, dict[str, Any]]]],
) -> bool:
    if (task.get("boundary") or {}).get("end_reason") != "explicit_completion":
        return False
    end_ts = _parse_ts(task.get("ended_at"))
    if end_ts is None:
        return False
    sid = str(task.get("session_id") or "")
    return any(
        abs(ts - end_ts) <= 1.25 and _is_agent_submit(e)
        for ts, e in raw_by_session.get(sid, [])
    )


def _raw_events_in_window(
    raw_by_session: dict[str, list[tuple[float, dict[str, Any]]]],
    task: dict[str, Any],
) -> list[dict[str, Any]]:
    start = _parse_ts(task.get("started_at"))
    end = _parse_ts(task.get("ended_at"))
    if start is None or end is None:
        return []
    sid = str(task.get("session_id") or "")
    return [
        e
        for ts, e in raw_by_session.get(sid, [])
        if start - 0.001 <= ts <= end + 0.001
    ]


def _best_context_label(events: list[dict[str, Any]], primary_surface: str) -> str:
    candidates: Counter[str] = Counter()
    for e in events:
        if e.get("event_type") != "focus_span":
            continue
        raw_title = str(e.get("window_title") or "").strip()
        cleaned = _clean_context_name(raw_title, primary_surface)
        if not cleaned:
            continue
        weight = max(1, int(round(float(e.get("duration_seconds") or 0))))
        if primary_surface and primary_surface.lower() in raw_title.lower():
            weight *= 3
        candidates[cleaned] += weight

    for e in _semantic_events(events):
        if _semantic_surface(e) != primary_surface:
            continue
        cleaned = _clean_context_name(_context_title(e), primary_surface)
        if cleaned:
            candidates[cleaned] += 2

    return candidates.most_common(1)[0][0] if candidates else ""


def _task_context_anchor(
    task: dict[str, Any],
    raw_by_session: dict[str, list[tuple[float, dict[str, Any]]]],
) -> str:
    primary = str(task.get("primary_surface") or "")
    label = _best_context_label(_raw_events_in_window(raw_by_session, task), primary)
    return analytics._normalize_task_anchor(label) if label else ""


def _merge_surface_order(a: list[Any], b: list[Any]) -> list[str]:
    out: list[str] = []
    for value in [*a, *b]:
        text = str(value)
        if text and (not out or out[-1] != text):
            out.append(text)
    return out


def _merge_tasks(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Merge only strongly evidenced adjacent slices; preserve the earlier task label."""
    out = dict(a)
    out["ended_at"] = b.get("ended_at")
    start_ts = _parse_ts(out.get("started_at")) or 0.0
    end_ts = _parse_ts(out.get("ended_at")) or start_ts
    out["elapsed_seconds"] = round(max(0.0, end_ts - start_ts), 3)
    for key in (
        "foreground_seconds",
        "engaged_seconds",
        "idle_seconds",
        "active_input_seconds",
    ):
        out[key] = round(float(a.get(key) or 0) + float(b.get(key) or 0), 3)
    for key in ("keypress_count", "click_count", "scroll_count", "semantic_action_count"):
        out[key] = int(a.get(key) or 0) + int(b.get(key) or 0)
    out["surfaces"] = _merge_surface_order(
        list(a.get("surfaces") or []), list(b.get("surfaces") or [])
    )
    actions: list[str] = []
    for value in [*(a.get("semantic_actions") or []), *(b.get("semantic_actions") or [])]:
        text = str(value)
        if text and (not actions or actions[-1] != text):
            actions.append(text)
    out["semantic_actions"] = actions[-48:]
    out["terminal_action"] = b.get("terminal_action") or a.get("terminal_action")
    out["completion_observed"] = bool(
        a.get("completion_observed") or b.get("completion_observed")
    )
    out["effort_estimated"] = bool(a.get("effort_estimated") or b.get("effort_estimated"))
    out["effort_attribution"] = (
        "overlap_proportional_estimate"
        if out["effort_estimated"]
        else "exact_focus_span"
    )
    out["boundary"] = {
        "start_reason": (a.get("boundary") or {}).get("start_reason"),
        "end_reason": (b.get("boundary") or {}).get("end_reason"),
        "confidence": (b.get("boundary") or {}).get("confidence"),
    }
    out["inference"] = "heuristic_candidate_v3_plus_context"
    out["needs_review"] = True
    return out


def _can_merge_agent_boundary(
    previous: dict[str, Any],
    following: dict[str, Any],
    raw_by_session: dict[str, list[tuple[float, dict[str, Any]]]],
) -> bool:
    """Conservative: same session, same agent surface, same non-empty context, <=90s."""
    if not _task_boundary_is_agent_submit(previous, raw_by_session):
        return False
    if str(previous.get("session_id") or "") != str(following.get("session_id") or ""):
        return False

    prev_end = _parse_ts(previous.get("ended_at"))
    next_start = _parse_ts(following.get("started_at"))
    if prev_end is None or next_start is None or not (0 <= next_start - prev_end <= 90):
        return False

    prev_surface = str(previous.get("primary_surface") or "")
    next_surface = str(following.get("primary_surface") or "")
    if not prev_surface or prev_surface != next_surface or not _is_agentic_surface(prev_surface):
        return False

    prev_anchor = _task_context_anchor(previous, raw_by_session)
    next_anchor = _task_context_anchor(following, raw_by_session)
    return bool(prev_anchor and next_anchor and prev_anchor == next_anchor)


def _outcomes(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for e in _semantic_events(events):
        if _is_agent_submit(e):
            continue
        label = (_target_label(e) or _semantic_action(e).replace("_", " ")).strip()
        if not label or not _OUTCOME_RE.search(label):
            continue
        key = f"{e.get('observed_at')}|{label}"
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "observed_at": e.get("observed_at"),
                "surface": _semantic_surface(e),
                "label": label,
                "evidence_event_id": _event_id(e),
            }
        )
    return out


def _decorate_task(
    task: dict[str, Any],
    raw_by_session: dict[str, list[tuple[float, dict[str, Any]]]],
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    out = dict(task)
    events = _raw_events_in_window(raw_by_session, out)
    outcomes = _outcomes(events)

    start = _parse_ts(out.get("started_at"))
    end = _parse_ts(out.get("ended_at"))
    turn_rows: list[dict[str, Any]] = []
    if start is not None and end is not None:
        for turn in turns:
            if str(turn.get("session_id") or "") != str(out.get("session_id") or ""):
                continue
            ts = _parse_ts(turn.get("submitted_at"))
            if ts is not None and start - 0.001 <= ts <= end + 0.001:
                turn_rows.append(turn)

    out["outcomes"] = outcomes
    out["agent_turn_count"] = len(turn_rows)
    out["agent_turn_ids"] = [t["agent_turn_id"] for t in turn_rows]
    out["evidence_window"] = {
        "session_id": out.get("session_id"),
        "started_at": out.get("started_at"),
        "ended_at": out.get("ended_at"),
    }
    out["anchor_event_ids"] = [
        item["evidence_event_id"] for item in outcomes
    ] + [t["evidence_event_id"] for t in turn_rows]
    out["task_id"] = hashlib.sha256(
        f"{out.get('session_id')}|{out.get('started_at')}|{out.get('ended_at')}|context-v1".encode()
    ).hexdigest()[:16]
    out["context_layer"] = "derived_regeneratable_v1"
    return out


def candidate_tasks(
    limit: int = 25000,
    since: str | None = None,
    *,
    gap_seconds: float = 120.0,
    max_task_seconds: float = 1800.0,
    _raw_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Add factual pointers/agent turns over existing v3 task inference.

    Existing v3 labels/families are retained. Only an explicit agent-submit boundary
    with the same session, same agent surface, same non-empty project/context anchor,
    and <=90 seconds between slices may be merged.
    """
    raw_events = (
        list(_raw_events)
        if _raw_events is not None
        else analytics._event_rows(limit, since=since)
    )
    base = analytics.candidate_tasks(
        limit=limit,
        since=since,
        gap_seconds=gap_seconds,
        max_task_seconds=max_task_seconds,
        _raw_events=raw_events,
    )
    raw_by_session = _events_by_session(raw_events)
    turns = agent_turns(limit=limit, since=since, _raw_events=raw_events)

    chronological = sorted(
        [dict(x) for x in base.get("tasks") or []],
        key=lambda x: str(x.get("started_at") or ""),
    )
    merged: list[dict[str, Any]] = []
    merge_count = 0
    for task in chronological:
        if merged and _can_merge_agent_boundary(merged[-1], task, raw_by_session):
            merged[-1] = _merge_tasks(merged[-1], task)
            merge_count += 1
        else:
            merged.append(task)

    enhanced = [_decorate_task(t, raw_by_session, turns) for t in merged]
    enhanced.sort(key=lambda x: str(x.get("started_at") or ""), reverse=True)

    return {
        "tasks": enhanced[:250],
        "patterns": list(base.get("patterns") or [])[:80],
        "agent_turns": turns[-500:],
        "factual_context": factual_context_timeline(
            limit=limit, since=since, _raw_events=raw_events
        )[-2000:],
        "inference": {
            **(base.get("inference") or {}),
            "context_layer": "derived_regeneratable_v1",
            "raw_evidence_mutated": False,
            "base_task_inference_rewritten": False,
            "agent_turns_are_separate_from_tasks": True,
            "same_context_agent_boundaries_merged": merge_count,
            "merge_requires_same_context_anchor": True,
            "merge_max_gap_seconds": 90,
            "evidence_windows_reference_raw_rows": True,
        },
    }


def apply_to_summary(
    result: dict[str, Any],
    *,
    limit: int = 10000,
    since: str | None = None,
) -> dict[str, Any]:
    """Compatibility hook: keep the hot summary/dashboard path unchanged and cheap."""
    return result
