"""Counts-only activity breakdown of ambiguous timestamp ordering."""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

MAX_ACTIVITIES = 100


def timestamp_ties(log_id: str, case_type: str | None = None, *,
                   cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    def run(conn):
        # ce represents case-event memberships. A shared OCEL event can occur
        # in multiple cases, so the unit throughout is a case-event occurrence.
        conn.execute("""CREATE TEMP TABLE tie_members AS
            SELECT case_id, event_id, activity, ts,
                   count(*) OVER (PARTITION BY case_id, ts) > 1 AS tied
            FROM ce""")
        occurrences, tied, cases, tied_cases = conn.execute("""SELECT count(*),
            count(*) FILTER (WHERE tied), count(DISTINCT case_id),
            count(DISTINCT case_id) FILTER (WHERE tied) FROM tie_members""").fetchone()
        rows = conn.execute("""SELECT activity, count(*) AS occurrences,
            count(*) FILTER (WHERE tied) AS tied_occurrences,
            count(DISTINCT case_id) FILTER (WHERE tied) AS tied_cases
            FROM tie_members GROUP BY activity
            ORDER BY tied_occurrences DESC, activity LIMIT ?""", [MAX_ACTIVITIES + 1]).fetchall()
        return {
            "format": "openworkgraph.event_log_timestamp_ties.v1",
            "case_event_occurrences": occurrences,
            "tied_case_event_occurrences": tied,
            "cases": cases, "cases_with_ties": tied_cases,
            "activities": [{"activity": a, "occurrences": n, "tied_occurrences": t,
                            "cases_with_ties": c} for a, n, t, c in rows[:MAX_ACTIVITIES]],
            "activities_truncated": len(rows) > MAX_ACTIVITIES,
            "interpretation": ("Events at the same timestamp within a case have unknown relative order; "
                "analyses use event identifiers as a deterministic tie-breaker. Counts are case-event "
                "occurrences, so an event shared by multiple cases is counted once in each case."),
        }
    return workspace._query_filtered(log_id, case_type, cohort, run)
