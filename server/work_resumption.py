"""Cross-session and cross-day work resumption candidates (roadmap #326 A3).

Answers "was the work I am doing now started in an earlier session?" from
already observed opaque pointers only, inside the same AI saved-history grant as
the continuity reference tail. Sessions are activity windows split by idle gaps;
they are not user-declared sessions and not tasks.

Confidence comes from shared stable pointers alone. How long ago an earlier
session happened is reported but never raises or lowers confidence, and a
session that shares no pointer with the current one is never a candidate,
however close in time it is. No titles, URLs, paths or contents are read.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from typing import Any

from .recent_resource_references import POINTER_FILTER, lease_window, sanitized_pointers

_UNIX_EPOCH_JULIAN_DAY = 2440587.5
MIN_GAP_MINUTES = 5
MAX_GAP_MINUTES = 12 * 60
MAX_ACTIVITY_ROWS = 200_000
MAX_POINTER_ROWS = 50_000


def _when(julian_day: float) -> datetime:
    # julianday() floats carry ~10µs of error; round so 10:00:00 never prints as 09:59:59.
    return datetime.fromtimestamp(round((julian_day - _UNIX_EPOCH_JULIAN_DAY) * 86400, 3), tz=timezone.utc)


def _iso(julian_day: float) -> str:
    return _when(julian_day).isoformat(timespec="seconds")


def _sessions(instants: list[float], gap_days: float) -> list[tuple[float, float]]:
    """Split ascending instants into (start, end) windows at idle gaps."""
    sessions: list[tuple[float, float]] = []
    for instant in instants:
        if sessions and instant - sessions[-1][1] <= gap_days:
            sessions[-1] = (sessions[-1][0], instant)
        else:
            sessions.append((instant, instant))
    return sessions


def _pointer(sanitized: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    resource = sanitized.get("resource_reference")
    if isinstance(resource, dict):
        out.append({key: resource[key] for key in ("resource_ref", "provider", "resource_kind")
                    if resource.get(key)})
    file_ref = sanitized.get("file_reference")
    if isinstance(file_ref, dict):
        out.append({"resource_ref": file_ref["file_ref"], "provider": "local_file", "resource_kind": "file"})
    return out


def _assess(specific: list[str], overlap: float) -> tuple[str, float]:
    """Status and confidence from pointer evidence only (never from time)."""
    if len(specific) >= 2 and overlap >= 0.34:
        return "likely_resumed", 0.8
    if len(specific) >= 2:
        return "possible", 0.65
    if specific:
        return "possible", 0.5
    # Only pointers that recur in most sessions (an always-open inbox or
    # dashboard) are shared: that says little about the same piece of work.
    return "ambiguous", 0.3


def resumption_candidates(
    connection: Any, *,
    lease: dict[str, Any],
    run_started_at: str | None = None,
    gap_minutes: int = 30,
    limit: int = 6,
    activity_budget: int = MAX_ACTIVITY_ROWS,
) -> dict[str, Any]:
    """Return earlier sessions that share stable pointers with the latest one."""
    gap_minutes = max(MIN_GAP_MINUTES, min(MAX_GAP_MINUTES, int(gap_minutes)))
    limit = max(1, min(20, int(limit)))
    activity_budget = max(100, min(MAX_ACTIVITY_ROWS, int(activity_budget)))
    mode, where, args = lease_window(lease, run_started_at)
    result: dict[str, Any] = {
        "current_session": None,
        "candidates": [],
        "saved_history_access": mode,
        "history_access_limited": mode != "all_saved",
        "session_gap_minutes": gap_minutes,
        "coverage": {"activity_rows_scanned": 0, "pointer_rows_scanned": 0, "sessions_considered": 0,
                     "scan_truncated": False, "no_candidate_proves_new_work": False},
        "association_contract": {
            "sessions_are_idle_gap_windows_not_tasks": True,
            "basis": "shared_stable_pointers",
            "time_alone_never_creates_a_candidate": True,
            "gap_affects_confidence": False,
            "same_task_identity_is_observed": False,
            "task_names_are_not_generated": True,
            "unfinished_state_is_inferred": False,
            "ask_when_ambiguous": True,
        },
        "derived": True,
        "authoritative": False,
    }
    if where is None:
        result["notice"] = "Saved-history AI access does not cover any session to compare."
        return result

    instants = [float(row[0]) for row in connection.execute(
        "SELECT julianday(observed_at) AS instant FROM events "
        f"WHERE julianday(observed_at) IS NOT NULL AND ({where}) "
        "ORDER BY instant DESC LIMIT ?", tuple(args + [activity_budget + 1])).fetchall()]
    truncated = len(instants) > activity_budget
    instants = sorted(instants[:activity_budget])
    result["coverage"]["activity_rows_scanned"] = len(instants)
    result["coverage"]["scan_truncated"] = truncated
    if not instants:
        return result
    sessions = _sessions(instants, gap_minutes / 1440)
    starts = [start for start, _end in sessions]
    result["coverage"]["sessions_considered"] = len(sessions)

    pointer_rows = connection.execute(
        "SELECT event_id, julianday(observed_at) AS instant, metadata_json FROM events "
        f"WHERE {POINTER_FILTER} AND julianday(observed_at) >= ? AND ({where}) "
        "ORDER BY instant DESC LIMIT ?", tuple([instants[0]] + args + [MAX_POINTER_ROWS])).fetchall()
    result["coverage"]["pointer_rows_scanned"] = len(pointer_rows)
    if len(pointer_rows) >= MAX_POINTER_ROWS:
        result["coverage"]["scan_truncated"] = True
    by_session: list[dict[str, Any]] = [{"refs": {}, "events": []} for _ in sessions]
    for row in pointer_rows:
        data = dict(row)
        instant = data.get("instant")
        if instant is None:
            continue
        index = bisect_right(starts, float(instant)) - 1
        if index < 0:
            continue
        pointers = _pointer(sanitized_pointers(data.get("metadata_json")))
        bucket = by_session[index]
        for pointer in pointers:
            bucket["refs"].setdefault(pointer["resource_ref"], pointer)
        if pointers and len(bucket["events"]) < 6:
            bucket["events"].append(str(data.get("event_id") or "")[:120])

    sessions_seen: dict[str, int] = {}
    for bucket in by_session:
        for ref in bucket["refs"]:
            sessions_seen[ref] = sessions_seen.get(ref, 0) + 1
    # A pointer present in most sessions is routine context, not a sign that one
    # piece of work continued.
    routine_threshold = max(3, (len(sessions) + 1) // 2)

    current_index = len(sessions) - 1
    current = by_session[current_index]
    current_start, current_end = sessions[current_index]
    result["current_session"] = {
        "started_at": _iso(current_start),
        "last_observed_at": _iso(current_end),
        "pointer_count": len(current["refs"]),
        "resource_refs": sorted(current["refs"])[:24],
    }
    if not current["refs"]:
        result["notice"] = ("The latest session has no observed stable pointer, so resumption "
                            "cannot be assessed; this does not mean the work is new.")
        return result

    candidates: list[dict[str, Any]] = []
    current_refs = set(current["refs"])
    for index in range(current_index - 1, -1, -1):
        earlier = by_session[index]
        shared = sorted(current_refs & set(earlier["refs"]))
        if not shared:
            continue
        specific = [ref for ref in shared if sessions_seen[ref] < routine_threshold]
        overlap = len(shared) / len(current_refs | set(earlier["refs"]))
        status, confidence = _assess(specific, overlap)
        start, end = sessions[index]
        gap_seconds = max(0.0, (current_start - end) * 86400)
        candidates.append({
            "earlier_session": {
                "started_at": _iso(start),
                "last_observed_at": _iso(end),
                "pointer_count": len(earlier["refs"]),
                "evidence_event_ids": earlier["events"],
            },
            "shared_pointers": [{**current["refs"][ref], "sessions_seen": sessions_seen[ref],
                                 "routine_context": ref not in specific} for ref in shared[:12]],
            "shared_pointer_count": len(shared),
            "pointer_overlap": round(overlap, 2),
            "gap_seconds": round(gap_seconds),
            "crosses_calendar_day": _when(end).date() != _when(current_start).date(),
            "association_status": status,
            "confidence": confidence,
            "proves_same_work": False,
            "task_label": None,
            "trace_window": {"since": _iso(start),
                             "until": (_when(end) + timedelta(seconds=1)).isoformat(timespec="seconds")},
        })
    # Strongest pointer evidence first; among equals the most recent earlier
    # session first. Order is navigation, not confidence.
    candidates.sort(key=lambda item: (-item["confidence"], -item["shared_pointer_count"], item["gap_seconds"]))
    for number, candidate in enumerate(candidates[:limit], start=1):
        candidate["candidate_id"] = f"resumption_candidate_{number:02d}"
    result["candidates"] = candidates[:limit]
    result["candidates_total"] = len(candidates)
    if truncated:
        result["notice"] = ("Only the newest activity inside the grant was scanned; "
                            "older sessions may also share these pointers.")
    return result


__all__ = ["resumption_candidates"]
