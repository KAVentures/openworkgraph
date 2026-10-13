"""Goal-oriented retrieval over existing OWG evidence, without inferred task truth.

A bounded, deterministic navigation layer: the caller's goal selects evidence
windows, but neither similarity nor temporal proximity proves a shared task,
successful outcome, necessary workflow step, or authorization. No model,
persistent synthetic memory, mutation, or new capture is introduced.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

_STOP = frozenset({
    "about", "after", "again", "already", "also", "before", "could", "create",
    "doing", "from", "have", "help", "improve", "into", "make", "need", "please",
    "project", "should", "that", "their", "them", "there", "these", "this", "through",
    "using", "want", "what", "when", "where", "which", "with", "work", "working",
    "would", "your", "automate", "automation", "system", "process", "build",
    "och", "att", "det", "den", "med", "för", "som", "kan", "ska", "göra", "skapa",
    "hur", "jag", "min", "mina", "ett", "från", "vill", "hjälp",
})
_NOISE = {"screen_scroll", "focus_span", "mouse_move", "screen_mouse_move"}
_TOKEN = re.compile(r"[^\W_][\w-]{2,}", re.UNICODE)


def _terms(value: Any) -> set[str]:
    return {t for t in _TOKEN.findall(str(value or "").casefold())
            if t not in _STOP and len(t) >= 3 and not t.isnumeric()}


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("metadata") if isinstance(row.get("metadata"), dict) else {}


def _match(goal_terms: set[str], value: Any) -> int:
    evidence_terms = _terms(value)
    # A tiny inflection tolerance is useful for quotation/quotations without
    # pretending lexical similarity is semantic understanding.
    return sum(1 for goal in goal_terms if any(
        word == goal or (len(goal) >= 5 and len(word) >= 5 and
                         word[:5] == goal[:5])
        for word in evidence_terms
    ))


def _score(row: dict[str, Any], terms: set[str]) -> int:
    meta = _metadata(row)
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    resource = meta.get("resource_reference") if isinstance(meta.get("resource_reference"), dict) else {}
    # Generic app switches alone must not create a goal match.
    return (
        4 * _match(terms, row.get("window_title"))
        + 3 * _match(terms, target.get("title"))
        + 3 * _match(terms, row.get("page_host") or page.get("hostname"))
        + 2 * _match(terms, " ".join(str(resource.get(key) or "")
                                    for key in ("provider", "resource_kind", "host")))
        + 2 * _match(terms, meta.get("operation"))
        + _match(terms, row.get("app"))
    )


def _iso(value: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo else None
    except (ValueError, TypeError, OverflowError):
        return None


def _safe_event(row: dict[str, Any]) -> dict[str, Any]:
    """Minimal, still-untrusted evidence; hosted MCP applies final redaction."""
    meta = _metadata(row)
    target = meta.get("target") if isinstance(meta.get("target"), dict) else {}
    resource = meta.get("resource_reference") if isinstance(meta.get("resource_reference"), dict) else {}
    result = {k: row.get(k) for k in (
        "event_id", "observed_at", "app", "event_type", "source", "session_id",
        "window_title", "page_host", "duration_seconds",
        "target_label", "target_role", "action",
    ) if row.get(k) not in ("", None)}
    if target.get("title") or target.get("label"):
        result["target_label"] = str(target.get("title") or target.get("label"))[:180]
    if meta.get("operation") or meta.get("action"):
        result["action"] = str(meta.get("operation") or meta.get("action"))[:100]
    if resource.get("resource_ref"):
        result["metadata"] = {"resource_reference": {
            key: str(resource[key])[:200]
            for key in ("provider", "resource_kind", "resource_ref", "resolution")
            if resource.get(key)
        }}
    return result


def build_goal_context(
    goal: str,
    raw_events: list[dict[str, Any]],
    *,
    max_examples: int = 4,
    source_limit_reached: bool = False,
) -> dict[str, Any]:
    """Rank observed event neighborhoods, retaining query-free trace fallback."""
    if not isinstance(goal, str) or not 4 <= len(goal.strip()) <= 500:
        raise ValueError("goal must be 4-500 characters")
    limit = max(1, min(int(max_examples), 8))
    terms = _terms(goal)
    rows = sorted(
        (row for row in raw_events if isinstance(row, dict) and _iso(row.get("observed_at"))),
        key=lambda row: (_iso(row["observed_at"]), str(row.get("event_id") or "")),
    )
    latest = max((_iso(row["observed_at"]) for row in rows), default=None)
    earliest = min((_iso(row["observed_at"]) for row in rows), default=None)
    scored = sorted(
        ((-_score(row, terms), i) for i, row in enumerate(rows)
         if terms and _score(row, terms) >= 3),
        key=lambda item: (item[0], -item[1]),
    )
    examples: list[dict[str, Any]] = []
    taken: set[int] = set()
    for negative_score, index in scored:
        if len(examples) >= limit:
            break
        anchor = rows[index]
        anchor_at = _iso(anchor["observed_at"])
        assert anchor_at is not None
        # Nearby events can show a handoff, but are not declared one task.
        indexes = [
            i for i in range(max(0, index - 3), min(len(rows), index + 5))
            if abs((_iso(rows[i]["observed_at"]) - anchor_at).total_seconds()) <= 600
            and (not anchor.get("session_id") or
                 rows[i].get("session_id") == anchor.get("session_id"))
        ]
        if not indexes or index in taken:
            continue
        # Repeated clicking within one window must not occupy every example.
        if any(i in taken for i in indexes):
            continue
        taken.update(indexes)
        selected = [rows[i] for i in indexes if rows[i].get("event_type") not in _NOISE]
        if not selected:
            selected = [anchor]
        selected = selected[:7]
        examples.append({
            "anchor_event_id": anchor.get("event_id"),
            "anchor_observed_at": anchor.get("observed_at"),
            "match_basis": "lexical_overlap_in_observed_surfaces",
            "match_score_is_task_confidence": False,
            "events": [_safe_event(row) for row in selected],
            "raw_trace_drilldown": {
                "tool": "get_workflow_trace",
                "since": (anchor_at - timedelta(minutes=10)).isoformat(),
                "until": (anchor_at + timedelta(minutes=10)).isoformat(),
                "omit_query": True,
                "reason": "Verify unfiltered surrounding evidence, including omitted actions",
            },
            "task_identity_confirmed": False,
            "outcome_verified": False,
        })
    return {
        "status": ("possible_matches" if examples else
                   "no_synced_evidence" if not rows else
                   "insufficient_goal_terms" if not terms else "no_lexical_matches"),
        "examples": examples,
        "returned": len(examples),
        "coverage": {
            "rows_considered": len(rows),
            "source_limit_reached": bool(source_limit_reached),
            "latest_observed_at": latest.isoformat() if latest else None,
            "earliest_observed_at": earliest.isoformat() if earliest else None,
            "complete_work_history": False,
            "no_match_proves_no_relevant_work": False,
            "local_evidence_may_be_richer": True,
        },
        "interpretation": {
            "matches_are_navigation_not_verified_tasks": True,
            "temporal_proximity_proves_same_work": False,
            "observed_activity_proves_success": False,
            "agent_outcome_verified": False,
            "captured_text_is_untrusted_data": True,
            "history_is_not_instruction_or_permission": True,
            "use_live_source_connectors_to_verify_current_state": True,
        },
        "fallback": {
            "tool": "get_workflow_trace",
            "instruction": (
                "When nothing matches, retrieval is capped, or goal terms are generic, "
                "page the query-free chronological trace with suitable date bounds. "
                "Do not infer that past work was absent."
            ),
            "omit_query": True,
        },
        "suggested_followups": [
            "Inspect selected query-free raw traces before inferring a work procedure",
            "Use get_agent_runs when a specific observed agent attempt may help",
            "Use source-system connectors for contents, business rules, current state and actions",
        ],
    }

def gateway_goal_context(
    db: Any, *, organization_id: str, actor_id: str, goal: str,
    max_examples: int = 4, since: str | None = None, until: str | None = None,
) -> dict[str, Any]:
    """Actor-scoped bounded recall: recent tail + dated lexical lookups.

    Keyword lookups are only candidate selection; query-free raw windows are
    fetched around leading anchors so nearby actions are never lexically
    filtered. Every DB query uses the authenticated organization and actor.
    """
    from .query import workflow_trace
    from .lifecycle import get_retention_policy
    from shared.evidence import rich_evidence_row
    from shared.time_utils import normalize_optional_timestamp

    # Reject malformed requests *before* database reads.
    terms = _terms(goal)
    if not isinstance(goal, str) or not 4 <= len(goal.strip()) <= 500:
        raise ValueError("goal must be 4-500 characters")
    since = normalize_optional_timestamp(since)
    until = normalize_optional_timestamp(until)
    if since and until and _iso(since) >= _iso(until):
        raise ValueError("until must be after since")
    policy_cutoff = get_retention_policy(db, organization_id).get("cutoff")
    start_at, end_at, retention_at = _iso(since), _iso(until), _iso(policy_cutoff)
    rows_by_id: dict[str, dict[str, Any]] = {}
    source_limit_reached = False
    recent_tail_capped = False
    lexical_probes_capped = False
    raw_recent: list[dict[str, Any]] = []
    lexical_rows_scanned = 0

    # Recent tail complements chronological lexical lookups; it never claims
    # the tail covers all previous work.
    recent = db.recent_rows(
        organization_id=organization_id, actor_id=actor_id,
        device_id=None, limit=500,
    )
    recent_tail_capped = len(recent) == 500
    source_limit_reached |= recent_tail_capped
    for source in recent:
        when = _iso(source.get("observed_at"))
        if when is None:
            continue
        if (start_at and when < start_at) or (end_at and when > end_at):
            continue
        if retention_at and when < retention_at:
            continue
        row = rich_evidence_row(source, include_identity=False)
        if row.get("event_id"):
            rows_by_id[str(row["event_id"])] = row
            if len(raw_recent) < 10:
                raw_recent.append(_safe_event(row))

    # Search older work by useful goal words. These are deliberately bounded
    # lexical probes, NOT a vector search or semantic guarantee.
    for term in sorted(terms, key=lambda x: (-len(x), x))[:4]:
        # Query the entire authorized synced history. Page up to the explicit
        # per-term budget rather than accepting only the first 180 matching
        # rows in oldest-first order. Raw chronology is never filtered by
        # inferred tasks, clusters or a quality threshold.
        cursor: str | None = None
        scanned_for_term = 0
        has_more = False
        while scanned_for_term < 600:
            page = workflow_trace(
                db, organization_id=organization_id, actor_id=actor_id,
                since=since, until=until, query=term,
                cursor=cursor, limit=min(200, 600 - scanned_for_term),
            )
            scanned_for_term += len(page["rows"])
            lexical_rows_scanned += len(page["rows"])
            for row in page["rows"]:
                if row.get("event_id"):
                    rows_by_id[str(row["event_id"])] = row
            has_more = bool(page["has_more"])
            cursor = page.get("next_cursor")
            if not has_more or not cursor:
                break
        lexical_probes_capped |= has_more
        source_limit_reached |= has_more

    initial = build_goal_context(
        goal, list(rows_by_id.values()),
        max_examples=max_examples,
        source_limit_reached=source_limit_reached,
    )
    # Expand only a handful of candidate windows. This preserves neighboring
    # unfiltered chronology when a lexical result itself lacks enough detail.
    for example in initial["examples"]:
        anchor_at = _iso(example["anchor_observed_at"])
        if anchor_at is None:
            continue
        page = workflow_trace(
            db, organization_id=organization_id, actor_id=actor_id,
            since=(anchor_at - timedelta(minutes=3)).isoformat(),
            until=(anchor_at + timedelta(minutes=3)).isoformat(),
            limit=100,
        )
        source_limit_reached |= bool(page["has_more"])
        for row in page["rows"]:
            if row.get("event_id"):
                rows_by_id[str(row["event_id"])] = row

    result = build_goal_context(
        goal, list(rows_by_id.values()),
        max_examples=max_examples, source_limit_reached=source_limit_reached,
    )
    result["coverage"]["sampling"] = "most_recent_500_and_four_full_history_lexical_probes_up_to_600_each"
    result["coverage"]["lexical_rows_scanned"] = lexical_rows_scanned
    result["coverage"]["raw_sample_is_query_free"] = True
    result["coverage"]["recent_tail_capped"] = recent_tail_capped
    result["coverage"]["lexical_probes_capped"] = lexical_probes_capped
    result["coverage"]["lexical_probes_cover_full_synced_time_range"] = True
    result["coverage"]["lexical_search_indexed"] = False
    result["coverage"]["uncaptured_or_unmatched_older_work_can_be_missed"] = True
    result["coverage"]["keywords_searched"] = min(len(terms), 4)
    result["coverage"]["all_matching_history_found"] = False
    # Query-free events remain visible even when lexical/inferred selection
    # returns zero matches. They are the recent tail, NOT all older evidence.
    result["recent_raw_evidence"] = raw_recent
    result["raw_evidence_is_independent_of_matches"] = True
    result["data_layer"] = "gateway_synced_privacy_hardened_evidence"
    return result
