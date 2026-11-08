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
# Resource-specific silence can be hidden by unrelated activity in a global session.
IDENTITY_INTERRUPTION_MINUTES = 5


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


def _identity(pointer: dict[str, Any]) -> tuple[str, str, str]:
    """Scope opaque pointer matching by provider and resource kind."""
    return (str(pointer.get("provider") or "").lower(),
            str(pointer.get("resource_kind") or "").lower(),
            str(pointer["resource_ref"]))


def _assess(specific: list[tuple[str, str, str]], overlap: float) -> tuple[str, float]:
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


def _identity_bursts(observations: list[tuple[float, str]], threshold_days: float) -> list[dict[str, Any]]:
    """Partition a resource's observed events; gaps are uncertainty, not task boundaries.

    A global idle-gap session can remain active while work on this identity stops.
    This returns bounded observed bursts, never interpolated activity or inferred work.
    """
    bursts: list[dict[str, Any]] = []
    for instant, event_id in sorted(observations):
        if not bursts or instant - bursts[-1]["last"] > threshold_days:
            bursts.append({"first": instant, "last": instant, "event_ids": []})
        burst = bursts[-1]
        burst["last"] = instant
        if len(burst["event_ids"]) < 6:
            burst["event_ids"].append(event_id)
    return bursts


def _visible_bursts(track: dict[str, Any], threshold_days: float) -> dict[str, Any]:
    bursts = _identity_bursts(track["observations"], threshold_days)
    selected = bursts[-20:]
    return {
        "segments": [{
            "first_observed_at": _iso(burst["first"]),
            "last_observed_at": _iso(burst["last"]),
            "evidence_event_ids": burst["event_ids"],
            "gap_from_previous_seconds": (
                max(0, round((burst["first"] - bursts[len(bursts) - len(selected) + i - 1]["last"]) * 86400))
                if len(bursts) - len(selected) + i > 0 else None
            ),
        } for i, burst in enumerate(selected)],
        "segments_truncated": len(bursts) > len(selected),
        "segments_are_observation_bursts_not_tasks": True,
    }


def _observed_case_groups(bucket: dict[str, Any]) -> list[dict[str, Any]]:
    """Partition observed resource identities by same-event co-reference only.

    Connected components are evidence-navigation groups, not confirmed cases:
    transitive co-reference does not prove a shared business task.
    """
    identities = sorted(bucket["refs"])
    adjacency = {identity: set() for identity in identities}
    for left, right in bucket["coobserved"]:
        if left in adjacency and right in adjacency:
            adjacency[left].add(right)
            adjacency[right].add(left)
    visited: set[tuple[str, str, str]] = set()
    groups: list[dict[str, Any]] = []
    for identity in identities:
        if identity in visited:
            continue
        pending = [identity]
        component: list[tuple[str, str, str]] = []
        visited.add(identity)
        while pending:
            node = pending.pop()
            component.append(node)
            for neighbor in sorted(adjacency[node]):
                if neighbor not in visited:
                    visited.add(neighbor)
                    pending.append(neighbor)
        component.sort()
        # For multi-resource groups, show only row-exact pair evidence.
        # A single-resource event on a member is NOT evidence that the
        # whole connected component was co-observed (especially for A-B-C
        # transitive chains). Keep singleton events for independent groups.
        if len(component) > 1:
            events = sorted({event_id for (left, right), pair_ids in bucket["pair_events"].items()
                             if left in component and right in component
                             for event_id in pair_ids if event_id})
        else:
            events = sorted({event_id for _instant, event_id in
                             bucket["tracks"][component[0]]["observations"] if event_id})
        groups.append({
            "group_id": f"observed_group_{len(groups) + 1:02d}",
            "resource_pointers": [bucket["refs"][node] for node in component],
            "resource_count": len(component),
            "evidence_event_ids": events[:12],
            "evidence_truncated": len(events) > 12,
            "observed_co_reference": len(component) > 1,
            "proves_same_case": False,
            "assignment_status": "observed_co_reference" if len(component) > 1 else "independent_resource",
        })
    return groups


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
        "identity_timelines": [],
        "observed_case_groups": [],
        "observed_case_groups_truncated": False,
        "observed_group_continuity": [],
        "observed_group_continuity_truncated": False,
        "observed_pair_event_assignments": [],
        "observed_pair_event_assignments_truncated": False,
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
            "identity_segments_are_observation_bursts_not_tasks": True,
            "resource_local_assignment_requires_correlated_observations": True,
            "observed_case_groups_are_not_confirmed_tasks": True,
            "group_continuity_requires_shared_observed_pair": True,
            "pair_event_assignments_require_same_event": True,
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
        # Only authorized empty windows receive an explicit empty review queue.
        from .reconstruction_review_cli import build_review_queue
        result["event_assignment_review"] = build_review_queue(result)
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
    by_session: list[dict[str, Any]] = [{"refs": {}, "events": [], "tracks": {}, "coobserved": set(), "pair_events": {}} for _ in sessions]
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
        identities_in_event = sorted({_identity(pointer) for pointer in pointers})
        for left_pos, left in enumerate(identities_in_event):
            for right in identities_in_event[left_pos + 1:]:
                bucket["coobserved"].add((left, right))
                event_id = str(data.get("event_id") or "")[:120]
                if event_id:
                    bucket["pair_events"].setdefault((left, right), set()).add(event_id)
        for pointer in pointers:
            identity = _identity(pointer)
            bucket["refs"].setdefault(identity, pointer)
            track = bucket["tracks"].setdefault(identity, {"first": float(instant),
                                                           "last": float(instant), "event_ids": [], "observations": []})
            track["first"] = min(track["first"], float(instant))
            track["last"] = max(track["last"], float(instant))
            track["observations"].append((float(instant), str(data.get("event_id") or "")[:120]))
            if len(track["event_ids"]) < 6:
                track["event_ids"].append(str(data.get("event_id") or "")[:120])
        if pointers and len(bucket["events"]) < 6:
            bucket["events"].append(str(data.get("event_id") or "")[:120])

    # Event IDs are external labels, not unique database-row identities. If
    # two observed rows reuse one label, neither row can safely be represented
    # by that label in an event-to-pair review. Fail closed for the colliding
    # label rather than merge distinct cases into a fabricated multi-pair event.
    # This also covers duplicate labels with different timestamps in one window.
    event_id_counts: dict[str, int] = {}
    for row in pointer_rows:
        event_id = str(row["event_id"] or "")[:120]
        if event_id:
            event_id_counts[event_id] = event_id_counts.get(event_id, 0) + 1
    colliding_event_ids = {key for key, count in event_id_counts.items() if count > 1}
    if colliding_event_ids:
        result["coverage"]["ambiguous_event_id_collisions"] = len(colliding_event_ids)
        result["coverage"]["scan_truncated"] = True
        for bucket in by_session:
            for pair, event_ids in bucket["pair_events"].items():
                event_ids.difference_update(colliding_event_ids)
            for track in bucket["tracks"].values():
                track["event_ids"] = [eid for eid in track["event_ids"] if eid not in colliding_event_ids]
                track["observations"] = [(t, eid) for t, eid in track["observations"]
                                         if eid not in colliding_event_ids]
            bucket["events"] = [eid for eid in bucket["events"] if eid not in colliding_event_ids]
            # A collided label cannot corroborate a pair across sessions either.
            # Preserve only edges backed by at least one unambiguous row label.
            bucket["coobserved"] = {pair for pair, ids in bucket["pair_events"].items() if ids}

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
        "resource_refs": sorted({identity[2] for identity in current["refs"]})[:24],
    }
    observed_groups = _observed_case_groups(current)
    result["observed_case_groups"] = observed_groups[:24]
    result["observed_case_groups_truncated"] = len(observed_groups) > 24
    # Corroborate current co-reference groups across sessions without treating
    # session-wide A/B overlap as a joint case. Only literal same-event pairs
    # observed in both windows qualify; no transitive group inference.
    group_matches: list[dict[str, Any]] = []
    for group in observed_groups:
        if group["resource_count"] < 2:
            continue
        group_ids = {_identity(pointer) for pointer in group["resource_pointers"]}
        for earlier_index in range(current_index - 1, -1, -1):
            earlier_bucket = by_session[earlier_index]
            pairs = sorted(pair for pair in current["coobserved"] & earlier_bucket["coobserved"]
                           if pair[0] in group_ids and pair[1] in group_ids)
            if not pairs:
                continue
            matched = sorted({identity for pair in pairs for identity in pair})
            earlier_start, earlier_end = sessions[earlier_index]
            group_matches.append({
                "current_group_id": group["group_id"],
                "earlier_session_started_at": _iso(earlier_start),
                "earlier_session_last_observed_at": _iso(earlier_end),
                "corroborated_pair_count": len(pairs),
                "matched_resource_pointers": [current["refs"][identity] for identity in matched[:12]],
                "matched_resources_truncated": len(matched) > 12,
                "assignment_status": "observed_pair_continuity_candidate",
                "proves_same_case": False,
                "requires_review": True,
            })
    # Exact event-to-pair assignments are narrower than transitive component
    # membership. A resource event without BOTH identities remains unassigned.
    # This is an observed co-reference, never a task or case assertion.
    pair_assignments: list[dict[str, Any]] = []
    for pair in sorted(current["coobserved"]):
        current_events = sorted(current["pair_events"].get(pair, ()))
        if not current_events:
            # A colliding external event label cannot safely identify the row.
            continue
        earlier_matches: list[dict[str, Any]] = []
        for earlier_index in range(current_index - 1, -1, -1):
            earlier = by_session[earlier_index]
            if pair not in earlier["coobserved"]:
                continue
            earlier_events = sorted(earlier["pair_events"].get(pair, ()))
            earlier_matches.append({
                "session_started_at": _iso(sessions[earlier_index][0]),
                "evidence_event_ids": earlier_events[:8],
                "evidence_truncated": len(earlier_events) > 8,
            })
        if not earlier_matches:
            # Without an earlier authorised session that saw the same pair there is
            # nothing to corroborate; history that is off or out of scope stays silent.
            continue
        pair_assignments.append({
            "resource_pointers": [current["refs"][node] for node in pair],
            "current_evidence_event_ids": current_events[:8],
            "current_evidence_truncated": len(current_events) > 8,
            "earlier_corroborations": earlier_matches[:8],
            "earlier_sessions_truncated": len(earlier_matches) > 8,
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
        })
    result["observed_pair_event_assignments"] = pair_assignments[:24]
    result["observed_pair_event_assignments_truncated"] = len(pair_assignments) > 24
    # Review-only event attribution under the same grant; never a confirmed case.
    from .reconstruction_review_cli import build_review_queue
    result["event_assignment_review"] = build_review_queue(result)
    result["observed_group_continuity"] = group_matches[:24]
    result["observed_group_continuity_truncated"] = len(group_matches) > 24
    if not current["refs"]:
        result["notice"] = ("The latest session has no observed stable pointer, so resumption "
                            "cannot be assessed; this does not mean the work is new.")
        return result

    # Each identity gets a separate chronological evidence trail, even when
    # idle-gap sessions multiplex unrelated cases. No time-only association.
    timelines: list[dict[str, Any]] = []
    for identity in sorted(current["refs"])[:12]:
        occurrences = [(i, bucket["tracks"][identity]) for i, bucket in enumerate(by_session)
                       if identity in bucket["tracks"]]
        if len(occurrences) < 2:
            continue
        selected = occurrences[-20:]
        episodes: list[dict[str, Any]] = []
        for position, (index, track) in enumerate(selected):
            previous_end = selected[position - 1][1]["last"] if position else None
            episodes.append({
                "first_observed_at": _iso(track["first"]),
                "last_observed_at": _iso(track["last"]),
                "idle_gap_session_started_at": _iso(sessions[index][0]),
                "evidence_event_ids": list(reversed(track["event_ids"])),
                **_visible_bursts(track, IDENTITY_INTERRUPTION_MINUTES / 1440),
                "gap_from_previous_seconds": (
                    max(0, round((track["first"] - previous_end) * 86400))
                    if previous_end is not None else None
                ),
            })
        timelines.append({
            "pointer": current["refs"][identity],
            "routine_context": sessions_seen[identity] >= routine_threshold,
            "episodes": episodes,
            "episodes_truncated": len(occurrences) > len(selected),
            "proves_same_task": False,
        })
    result["identity_timelines"] = timelines

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
        # Session-wide overlap may combine unrelated cases A and B.
        # Only simultaneous observations in BOTH sessions corroborate a pair.
        correlated_pairs = sorted(current["coobserved"] & earlier["coobserved"])
        correlated_specific = [pair for pair in correlated_pairs
                               if pair[0] in specific and pair[1] in specific]
        if correlated_specific:
            assignment_status = "corroborated_candidate"
            # A pair observed together in both sessions is stronger than a
            # union of unrelated resource pointers. It is still not a task.
            status, confidence = _assess(specific, overlap)
        elif specific:
            assignment_status = "unresolved_independent_resources"
            # Independent A/B resources can make session-wide Jaccard 1.0.
            # Do not rank that as a likely resumed *case* without observed
            # resource-local corroboration. A single stable resource remains
            # a possible continuation, never a proven task.
            status, confidence = "possible", (0.5 if len(specific) == 1 else 0.55)
        else:
            assignment_status = "ambiguous_routine_context"
            status, confidence = "ambiguous", 0.3
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
            # A session may multiplex unrelated cases. Evidence tracks keep
            # each matched identity independent instead of presenting the
            # whole idle-gap window as evidence for every resumed case.
            "pointer_tracks": [
                {
                    "pointer": current["refs"][ref],
                    "earlier": {
                        "first_observed_at": _iso(earlier["tracks"][ref]["first"]),
                        "last_observed_at": _iso(earlier["tracks"][ref]["last"]),
                        "evidence_event_ids": earlier["tracks"][ref]["event_ids"],
                        **_visible_bursts(earlier["tracks"][ref], IDENTITY_INTERRUPTION_MINUTES / 1440),
                    },
                    "current": {
                        "first_observed_at": _iso(current["tracks"][ref]["first"]),
                        "last_observed_at": _iso(current["tracks"][ref]["last"]),
                        "evidence_event_ids": current["tracks"][ref]["event_ids"],
                        **_visible_bursts(current["tracks"][ref], IDENTITY_INTERRUPTION_MINUTES / 1440),
                    },
                }
                for ref in shared[:12]
            ],
            "shared_pointer_count": len(shared),
            "pointer_overlap": round(overlap, 2),
            "gap_seconds": round(gap_seconds),
            "crosses_calendar_day": _when(end).date() != _when(current_start).date(),
            "association_status": status,
            "case_assignment_status": assignment_status,
            "case_assignment_proven": False,
            "correlated_resource_pair_count": len(correlated_specific),
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
