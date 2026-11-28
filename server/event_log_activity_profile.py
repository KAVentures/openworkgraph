"""Activity profile: one recorded activity seen from every side.

Counts, neighbours, UTC timing and case-duration comparisons are computed in
DuckDB so an analysis does not copy the complete event log into Python.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_activity_profile.v1"
TOP = 5
_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _hours(seconds: float | None) -> float | None:
    return round(seconds / 3600, 3) if seconds is not None else None


def activity_profile(log_id: str, activity: str, case_type: str | None = None, *,
                     cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(activity, str) or not activity or len(activity) > 500:
        raise EventLogWorkspaceError("choose an activity")

    def run(conn):
        # A timestamp tie is ordered by the same event identifier used throughout
        # the workspace; the two boundary markers remain distinct from labels.
        conn.execute("""CREATE TEMP TABLE ap_events AS SELECT case_id, activity, ts,
            lag(activity) OVER trace AS previous_activity, lag(ts) OVER trace AS previous_ts,
            lead(activity) OVER trace AS next_activity, lead(ts) OVER trace AS next_ts
            FROM ce WINDOW trace AS (PARTITION BY case_id ORDER BY ts, event_id)""")
        conn.execute("""CREATE TEMP TABLE ap_cases AS SELECT case_id,
            count(*) FILTER (WHERE activity = ?) AS selected_count,
            epoch(max(ts)) - epoch(min(ts)) AS duration,
            epoch(min(ts) FILTER (WHERE activity = ?)) - epoch(min(ts)) AS first_offset
            FROM ap_events GROUP BY case_id""", [activity, activity])
        total, with_cases, occurrences, repeat_cases = conn.execute("""SELECT count(*),
            count(*) FILTER (WHERE selected_count > 0), coalesce(sum(selected_count), 0),
            count(*) FILTER (WHERE selected_count > 1) FROM ap_cases""").fetchone()
        if not with_cases:
            raise EventLogWorkspaceError(f"no case in this selection has the activity {activity!r}")
        first_med, first_p90, with_med, with_p90, without_med, without_p90 = conn.execute("""
            SELECT quantile_cont(first_offset, 0.5), quantile_cont(first_offset, 0.9),
                quantile_cont(duration, 0.5) FILTER (WHERE selected_count > 0),
                quantile_cont(duration, 0.9) FILTER (WHERE selected_count > 0),
                quantile_cont(duration, 0.5) FILTER (WHERE selected_count = 0),
                quantile_cont(duration, 0.9) FILTER (WHERE selected_count = 0)
            FROM ap_cases""").fetchone()

        def neighbours(label_column: str, gap: str, boundary: str) -> list[dict[str, Any]]:
            # Both identifiers are fixed by the caller; activity itself is bound.
            rows = conn.execute(f"""SELECT {label_column} AS neighbour, count(*) AS n,
                    quantile_cont({gap}, 0.5) AS median_gap
                FROM ap_events WHERE activity = ? GROUP BY neighbour
                ORDER BY n DESC, neighbour IS NULL, neighbour LIMIT {TOP}""", [activity]).fetchall()
            return [{"activity": label if label is not None else boundary, "times": n,
                     "share": round(n / occurrences, 4), "median_hours": _hours(median),
                     **({"boundary": True} if label is None else {})}
                    for label, n, median in rows]

        before = neighbours("previous_activity", "epoch(ts) - epoch(previous_ts)", "(start)")
        after = neighbours("next_activity", "epoch(next_ts) - epoch(ts)", "(end)")
        weekdays = dict(conn.execute("""SELECT isodow(ts), count(*) FROM ap_events
            WHERE activity = ? GROUP BY 1""", [activity]).fetchall())
        hours = dict(conn.execute("""SELECT hour(ts), count(*) FROM ap_events
            WHERE activity = ? GROUP BY 1""", [activity]).fetchall())
        return {"profile": {
            "format": FORMAT, "log_id": log_id, "activity": activity,
            "cases": total, "cases_with_activity": with_cases, "share_of_cases": round(with_cases / total, 4),
            "occurrences": occurrences, "occurrences_per_case": round(occurrences / with_cases, 3),
            "cases_repeating_it": repeat_cases, "repeat_share": round(repeat_cases / with_cases, 4),
            "comes_after": before, "comes_before": after,
            "hours_from_case_start_to_first": {"median": _hours(first_med), "p90": _hours(first_p90)},
            "case_hours": {"with_activity": {"median": _hours(with_med), "p90": _hours(with_p90)},
                           "without_activity": {"median": _hours(without_med), "p90": _hours(without_p90)}},
            "by_weekday": [{"day": d, "times": weekdays.get(i, 0)} for i, d in enumerate(_WEEKDAYS, 1)],
            "by_hour": [{"hour": h, "times": hours.get(h, 0)} for h in range(24)],
            "caveat": ("Counts and durations of recorded events. Cases with the activity can take longer for many reasons; "
                       "times of day are in the log's stored time zone (UTC)."),
        }}
    return workspace._query_filtered(log_id, case_type, cohort, run)["profile"]


__all__ = ["activity_profile", "FORMAT"]
