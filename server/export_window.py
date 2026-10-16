"""Validated export selection, shared by previews, downloads and download tickets."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
import copy
import os


def timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValueError("Use an ISO date/time with a timezone") from None
    if parsed.tzinfo is None:
        raise ValueError("Date/time must include a timezone")
    return parsed.astimezone(timezone.utc)


def observed_timestamp(value: str) -> datetime:
    # Historical rows may predate timezone-qualified capture. SQLite and the
    # existing analytics interpret those as UTC; keep that legacy convention.
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def selection(scope: str = "current", since: str | None = None,
              until: str | None = None) -> dict[str, Any]:
    if scope not in {"current", "all", "range"}:
        raise ValueError("scope must be current, all, or range")
    if scope == "range":
        if not since or not until:
            raise ValueError("Choose both start and end dates")
        start, end = timestamp(since), timestamp(until)
        if start >= end:
            raise ValueError("End must be after start")
        since, until = start.isoformat(), end.isoformat()
    elif since is not None or until is not None:
        raise ValueError("Date bounds require scope=range")
    else:
        since = os.getenv("WORKFLOW_OBSERVER_RUN_STARTED_AT") if scope == "current" else None
    return {"scope": scope, "since": since, "until": until,
            "bounds": "start_inclusive_end_exclusive", "timezone": "UTC",
            "event_selection": "observed_at_in_window"}


def sql_bounds(since: str | None, until: str | None, column: str = "observed_at"):
    # Column is supplied by code, never a request parameter.
    parts, params = [], []
    for bound, op in ((since, ">="), (until, "<")):
        if bound:
            parts.append(f"julianday({column}) {op} julianday(?)")
            params.append(bound)
    return " AND ".join(parts), tuple(params)


def analysis_rows(events: list[dict[str, Any]], until: str | None) -> list[dict[str, Any]]:
    """Clip analysis copies of spans; canonical exported observations stay intact."""
    if not until:
        return events
    end = timestamp(until).timestamp()
    out = copy.deepcopy(events)
    for event in out:
        duration = max(0.0, float(event.get("duration_seconds") or 0))
        if not duration:
            continue
        kept = min(duration, max(0.0, end - observed_timestamp(event["observed_at"]).timestamp()))
        if kept < duration:
            event["duration_seconds"] = kept
            activity = (event.get("metadata") or {}).get("activity") or {}
            for key in ("engaged_seconds", "idle_seconds", "active_input_seconds",
                        "keypress_count", "click_count", "scroll_count"):
                if key in activity:
                    activity[key] *= kept / duration
    return out
