"""Bounded per-execution coverage of retained foreground spans.

Only the already selected event window is read. This is wall-clock union
coverage, not active labor, waiting, case membership or verified outcomes.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from typing import Any, Callable

MAX_SURFACES = 30


def _stamp(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        result = parsed.timestamp()
        return result if math.isfinite(result) else None
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _positive_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def execution_foreground_coverage(
    execution: dict[str, Any],
    events: list[dict[str, Any]],
    surface_token: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    """Clip spans to the execution window and union overlapping intervals.

    Overlaps of distinct surface tokens remain ambiguous rather than being
    attributed twice. Uncovered time is unknown, not idle or process waiting.
    Only spans present in the selected evidence are used; a span beginning
    before the requested evidence window may not be available.
    """
    start = _stamp(execution.get("started_at"))
    end = _stamp(execution.get("ended_at"))
    row = {
        "execution_id": execution.get("execution_id"),
        "actor_kind": execution.get("actor_kind"),
        "observed_event_count": len(events),
        "status": "unavailable",
        "window_seconds": None,
        "foreground_union_seconds": None,
        "overlapping_distinct_surface_seconds": None,
        "uncovered_window_seconds": None,
        "foreground_span_count": 0,
        "invalid_span_count": 0,
        "surface_rows": [],
        "surface_rows_omitted": 0,
        "exclusive_other_surface_seconds": 0.0,
        "membership_verified": False,
        "coverage_complete": False,
    }
    if start is None or end is None or end <= start:
        return row
    row["window_seconds"] = round(end - start, 3)
    # Agents need not have a foreground app. Never present a lack of focus
    # spans in an agent run as zero effort or zero elapsed time.
    if execution.get("actor_kind") != "human":
        row["status"] = "not_applicable"
        return row
    changes: dict[float, Counter[str]] = defaultdict(Counter)
    for event in events:
        if event.get("event_type") not in {"focus_span", "focus_period"}:
            continue
        at = _stamp(event.get("observed_at"))
        duration = _positive_seconds(event.get("duration_seconds"))
        if at is None or duration is None:
            row["invalid_span_count"] += 1
            continue
        lo, hi = max(start, at), min(end, at + duration)
        if hi <= lo:
            continue
        token = surface_token(event)
        if not isinstance(token, str) or not token.startswith("surface:"):
            token = "surface:unknown"
        changes[lo][token] += 1
        changes[hi][token] -= 1
        row["foreground_span_count"] += 1
    if not changes:
        row["status"] = "no_retained_foreground_spans"
        return row
    active: Counter[str] = Counter()
    exclusive: Counter[str] = Counter()
    covered = overlap = 0.0
    previous = start
    for at in sorted(changes):
        seconds = at - previous
        if active:
            covered += seconds
            if len(active) == 1:
                exclusive[next(iter(active))] += seconds
            else:
                overlap += seconds
        for token, delta in changes[at].items():
            active[token] += delta
            if active[token] <= 0:
                del active[token]
        previous = at
    ranked = sorted(exclusive.items(), key=lambda item: (-item[1], item[0]))
    row.update(
        status="observed_bounded_coverage",
        foreground_union_seconds=round(covered, 3),
        overlapping_distinct_surface_seconds=round(overlap, 3),
        uncovered_window_seconds=round(max(0.0, end - start - covered), 3),
        surface_rows=[
            {"surface": token, "exclusive_foreground_seconds": round(seconds, 3)}
            for token, seconds in ranked[:MAX_SURFACES]
        ],
        surface_rows_omitted=max(0, len(ranked) - MAX_SURFACES),
        exclusive_other_surface_seconds=round(sum(value for _, value in ranked[MAX_SURFACES:]), 3),
    )
    return row


def build_foreground_coverage(
    selected: list[dict[str, Any]],
    per_run_events: dict[str, list[dict[str, Any]]],
    surface_token: Callable[[dict[str, Any]], str],
) -> dict[str, Any]:
    return {
        "format": "openworkgraph.execution-foreground-coverage.v1",
        "runs": [
            execution_foreground_coverage(
                run, per_run_events.get(str(run.get("execution_id") or ""), []), surface_token
            )
            for run in selected
        ],
        "basis": "union_of_retained_focus_spans_clipped_to_inferred_execution_window",
        "uncovered_time_is_unknown": True,
        "distinct_surface_overlaps_are_unattributed": True,
        "sums_across_runs_are_not_unique_time": True,
        "active_labor_or_waiting_measured": False,
    }
