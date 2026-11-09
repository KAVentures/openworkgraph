"""How recorded time was split up: focus blocks and app switches from focus spans.

Consecutive focus spans in the same app, with gaps no longer than ``max_gap_seconds`` and
detours to another app shorter than ``ignore_detours_seconds``, form a **focus block**; its
length is the elapsed time from its first to its last span, short detours included. The
report gives, per local day in the chosen time zone, the number of app switches, switches
per active hour, and the median and longest focus blocks, plus the app pairs switched
between most and the longest blocks overall.

This is the person's own view of their own recorded activity. Switching is not a measure of
attention, productivity or quality of work, and many jobs need frequent switching.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import statistics
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_DAYS = 92


class FocusReportError(ValueError):
    pass


def _spans(start_utc: datetime, end_utc: datetime) -> list[tuple[datetime, datetime, str]]:
    from .db import connect
    with connect() as conn:
        rows = conn.execute("""SELECT observed_at, duration_seconds, app FROM events WHERE event_type = 'focus_span'
            AND julianday(observed_at) >= julianday(?) AND julianday(observed_at) < julianday(?)
            ORDER BY observed_at""", ((start_utc - timedelta(days=1)).isoformat(), end_utc.isoformat())).fetchall()
    out = []
    for observed, duration, app in rows:
        try:
            at = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
        except ValueError:
            continue
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        seconds = max(0.0, float(duration or 0))
        if not app or not seconds:
            continue
        lo, hi = max(at, start_utc), min(at + timedelta(seconds=seconds), end_utc)
        if hi > lo:
            out.append((lo, hi, str(app)))
    return out


def report(*, start: str, end: str, tz: str = "UTC", max_gap_seconds: int = 120,
           ignore_detours_seconds: int = 30) -> dict[str, Any]:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise FocusReportError("unknown time zone") from None
    try:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError):
        raise FocusReportError("start and end must be dates (YYYY-MM-DD)") from None
    if last < first:
        raise FocusReportError("end must not be before start")
    if (last - first).days + 1 > MAX_DAYS:
        raise FocusReportError(f"a report covers at most {MAX_DAYS} days")
    if not 0 <= max_gap_seconds <= 3600 or not 0 <= ignore_detours_seconds <= 600:
        raise FocusReportError("max_gap_seconds is 0-3600 and ignore_detours_seconds 0-600")
    window_start = datetime.combine(first, datetime.min.time(), zone).astimezone(timezone.utc)
    window_end = datetime.combine(last + timedelta(days=1), datetime.min.time(), zone).astimezone(timezone.utc)
    spans = _spans(window_start, window_end)
    # Detours shorter than the threshold are folded into the surrounding block when it resumes.
    blocks: list[dict[str, Any]] = []
    switches: Counter = Counter()
    per_day_switches: dict[str, int] = defaultdict(int)
    for lo, hi, app in spans:
        current = blocks[-1] if blocks else None
        if current and app == current["app"] and (lo - current["end"]).total_seconds() <= max_gap_seconds:
            current["end"] = max(current["end"], hi)
            current["seconds"] = (current["end"] - current["start"]).total_seconds()
            continue
        if (current and len(blocks) >= 2 and blocks[-2]["app"] == app and current["seconds"] < ignore_detours_seconds
                and (lo - current["end"]).total_seconds() <= max_gap_seconds):
            detour = blocks.pop()
            if detour.get("switch"):
                pair, day = detour["switch"]
                switches[pair] -= 1
                per_day_switches[day] -= 1
                if not switches[pair]:
                    del switches[pair]
            previous = blocks[-1]
            previous["end"] = max(previous["end"], hi)
            previous["seconds"] = (previous["end"] - previous["start"]).total_seconds()
            previous["detours"] = previous.get("detours", 0) + 1
            previous["detour_seconds"] = previous.get("detour_seconds", 0.0) + detour["seconds"]
            continue
        switch = None
        if current and (lo - current["end"]).total_seconds() <= max_gap_seconds and app != current["app"]:
            switch = (tuple(sorted((current["app"], app))), lo.astimezone(zone).date().isoformat())
            switches[switch[0]] += 1
            per_day_switches[switch[1]] += 1
        blocks.append({"app": app, "start": lo, "end": hi, "seconds": (hi - lo).total_seconds(), "switch": switch})
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        by_day[block["start"].astimezone(zone).date().isoformat()].append(block)
    days = []
    cursor = first
    while cursor <= last:
        key = cursor.isoformat()
        day_blocks = by_day.get(key, [])
        active = sum(b["seconds"] for b in day_blocks)
        lengths = [b["seconds"] for b in day_blocks]
        days.append({"date": key, "recorded_seconds": round(active, 1), "focus_blocks": len(day_blocks),
                     "app_switches": per_day_switches.get(key, 0),
                     "switches_per_active_hour": round(per_day_switches.get(key, 0) / (active / 3600), 2) if active >= 600 else None,
                     "median_block_seconds": round(statistics.median(lengths), 1) if lengths else None,
                     "longest_block_seconds": round(max(lengths), 1) if lengths else None})
        cursor += timedelta(days=1)
    lengths = [b["seconds"] for b in blocks]
    longest = sorted(blocks, key=lambda b: -b["seconds"])[:10]
    return {
        "format": "openworkgraph.focus-report.v1", "start": first.isoformat(), "end": last.isoformat(), "time_zone": tz,
        "settings": {"max_gap_seconds": max_gap_seconds, "ignore_detours_seconds": ignore_detours_seconds},
        "focus_blocks": len(blocks), "app_switches": sum(switches.values()),
        "median_block_seconds": round(statistics.median(lengths), 1) if lengths else None,
        "blocks_over_25_minutes": sum(1 for x in lengths if x >= 1500),
        "days": days,
        "top_switch_pairs": [{"apps": list(pair), "switches": n} for pair, n in
                             sorted(switches.items(), key=lambda p: (-p[1], p[0]))[:10]],
        "longest_blocks": [{"app": b["app"], "start": b["start"].astimezone(zone).isoformat(timespec="minutes"),
                            "seconds": round(b["seconds"], 1), "short_detours": b.get("detours", 0)} for b in longest],
        "interpretation": ("How recorded foreground time was split between apps. Switching is not a measure of "
                           "attention, productivity or quality of work, and many jobs need it."),
    }


__all__ = ["FocusReportError", "report"]
