"""Where observed time went: a local time-allocation report from focus spans.

Each recorded focus span (an app in the foreground) has a start, a duration and, where the
collector measured it, engaged and idle seconds. The report adds them up per local day and
per app, in the time zone the person chooses; a span that crosses midnight is split at the
day boundary. Hours of the day show when the time fell.

This is the person's own view of their own recorded activity. Foreground time is not effort,
productivity or billable time, and gaps are times nothing was recorded (paused, away or not
captured), not proof of no work. Window titles are left out unless asked for.
"""
from __future__ import annotations

from collections import defaultdict
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_DAYS = 92
MAX_APPS = 50
MAX_TITLES = 20


class TimeAllocationError(ValueError):
    pass


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise TimeAllocationError("unknown time zone") from None


def _parse_day(value: str, what: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise TimeAllocationError(f"{what} must be a date (YYYY-MM-DD)") from None


def _spans(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, float, str, dict, str]]:
    from .db import connect
    # A span can start before the window and run into it; look back one day.
    with connect() as conn:
        rows = conn.execute("""SELECT observed_at, duration_seconds, app, metadata_json, window_title FROM events
            WHERE event_type = 'focus_span' AND julianday(observed_at) >= julianday(?) AND julianday(observed_at) < julianday(?)
            ORDER BY observed_at""", ((start_utc - timedelta(days=1)).isoformat(), end_utc.isoformat())).fetchall()
    out = []
    for observed, duration, app, meta_json, title in rows:
        try:
            at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        try:
            meta = json.loads(meta_json or "{}")
        except ValueError:
            meta = {}
        seconds = max(0.0, float(duration or 0))
        if seconds and app:
            out.append((at, seconds, str(app), meta if isinstance(meta, dict) else {}, str(title or "")))
    return out


def report(*, start: str, end: str, tz: str = "UTC", include_titles: bool = False) -> dict[str, Any]:
    zone = _zone(tz)
    first, last = _parse_day(start, "start"), _parse_day(end, "end")
    if last < first:
        raise TimeAllocationError("end must not be before start")
    if (last - first).days + 1 > MAX_DAYS:
        raise TimeAllocationError(f"a report covers at most {MAX_DAYS} days")
    window_start = datetime.combine(first, datetime.min.time(), zone)
    window_end = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone)
    days: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    engaged: dict[str, float] = defaultdict(float)
    idle: dict[str, float] = defaultdict(float)
    hours = [0.0] * 24
    titles: dict[tuple[str, str], float] = defaultdict(float)
    spans_used = 0
    for at, seconds, app, meta, title in _spans(window_start.astimezone(timezone.utc), window_end.astimezone(timezone.utc)):
        span_start, span_end = at, at + timedelta(seconds=seconds)
        lo, hi = max(span_start, window_start), min(span_end, window_end)
        if hi <= lo:
            continue
        spans_used += 1
        share = (hi - lo).total_seconds() / seconds
        activity = meta.get("activity") if isinstance(meta.get("activity"), dict) else {}
        engaged[app] += float(activity.get("engaged_seconds") or 0) * share
        idle[app] += float(activity.get("idle_seconds") or 0) * share
        if include_titles and title:
            titles[(app, title[:200])] += (hi - lo).total_seconds()
        cursor = lo
        while cursor < hi:
            local = cursor.astimezone(zone)
            next_hour = (local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)).astimezone(timezone.utc)
            piece_end = min(hi, next_hour)
            piece = (piece_end - cursor).total_seconds()
            days[local.date().isoformat()][app] += piece
            hours[local.hour] += piece
            cursor = piece_end
    app_totals: dict[str, float] = defaultdict(float)
    for per_app in days.values():
        for app, seconds in per_app.items():
            app_totals[app] += seconds
    ranked = sorted(app_totals.items(), key=lambda p: (-p[1], p[0]))
    top = {a for a, _ in ranked[:MAX_APPS]}
    day_rows = []
    cursor_day = first
    while cursor_day <= last:
        key = cursor_day.isoformat()
        per_app = days.get(key, {})
        shown = {a: round(s, 1) for a, s in sorted(per_app.items(), key=lambda p: (-p[1], p[0])) if a in top}
        other = sum(s for a, s in per_app.items() if a not in top)
        day_rows.append({"date": key, "recorded_seconds": round(sum(per_app.values()), 1), "apps": shown,
                         **({"other_apps_seconds": round(other, 1)} if other else {})})
        cursor_day += timedelta(days=1)
    total = sum(app_totals.values())
    out: dict[str, Any] = {
        "format": "openworkgraph.time-allocation.v1", "start": first.isoformat(), "end": last.isoformat(), "time_zone": tz,
        "recorded_seconds": round(total, 1), "spans": spans_used, "days": day_rows,
        "apps": [{"app": a, "recorded_seconds": round(s, 1), "share": round(s / total, 4) if total else 0.0,
                  "engaged_seconds": round(engaged[a], 1), "idle_seconds": round(idle[a], 1)} for a, s in ranked[:MAX_APPS]],
        "apps_truncated": len(ranked) > MAX_APPS,
        "hours_of_day_seconds": [round(h, 1) for h in hours],
        "titles_included": include_titles,
        "interpretation": ("Foreground time recorded on this computer, split by local day. It is not effort, "
                           "productivity or billable time, and gaps are times nothing was recorded, not proof of "
                           "no work."),
    }
    if include_titles:
        out["top_window_titles"] = [{"app": a, "title": t, "recorded_seconds": round(s, 1)}
                                    for (a, t), s in sorted(titles.items(), key=lambda p: (-p[1], p[0]))[:MAX_TITLES]]
    return out


def to_csv(result: dict[str, Any]) -> str:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["date", "app", "recorded_hours"])
    for day in result["days"]:
        for app, seconds in day["apps"].items():
            writer.writerow([day["date"], ("'" + app) if app[:1] in "=+-@\t\r" else app, round(seconds / 3600, 3)])
        if day.get("other_apps_seconds"):
            writer.writerow([day["date"], "(other apps)", round(day["other_apps_seconds"] / 3600, 3)])
    return out.getvalue()


__all__ = ["TimeAllocationError", "report", "to_csv"]
