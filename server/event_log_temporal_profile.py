"""Temporal profile: the usual time between activities, and the cases that break it.

A temporal profile (Stertz, Mangler and Rinderle-Ma, 2020; PM4Py's temporal profile discovery
and conformance) records, for every ordered pair of activities that occur in the same case, the
mean and standard deviation of the time from the first to the second. A case deviates on a pair
when its time differs from the mean by more than ``zeta`` standard deviations. Unlike
directly-follows durations, this also catches activities that are far apart: an invoice that comes
weeks after the order even though the steps in between look normal.

This module:

1. pairs every event with the events after it in the same case, up to ``MAX_SPAN`` events later;
2. keeps the pairs seen at least ``min_occurrences`` times with some spread (standard deviation
   above zero) as the profile;
3. checks every occurrence against its pair's profile and reports the pairs with most deviations
   and the cases with most deviations, each with its strongest deviation.

The profile is learned from the same cases it checks, as in PM4Py; a case that deviates a lot
pulls the mean towards itself, so the check is conservative for small logs.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_temporal_profile.v1"
MAX_SPAN = 50
DEFAULT_ZETA = 3.0
DEFAULT_MIN_OCCURRENCES = 5
TOP_PAIRS = 50
TOP_CASES = 25


def _validate(zeta: float, min_occurrences: int) -> None:
    if isinstance(zeta, bool) or not isinstance(zeta, (int, float)) or not 0.5 <= zeta <= 10:
        raise EventLogWorkspaceError("zeta must be a number of standard deviations from 0.5 to 10")
    if type(min_occurrences) is not int or not 2 <= min_occurrences <= 10_000:
        raise EventLogWorkspaceError("min_occurrences must be an integer from 2 to 10000")


def _prepare(conn, zeta: float, min_occurrences: int, pair: tuple[str, str] | None = None) -> None:
    where = " WHERE a.activity = ? AND b.activity = ?" if pair is not None else ""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE tp_pairs AS
        WITH e AS (SELECT case_id, activity, ts,
                          row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS rn FROM ce)
        SELECT a.case_id, a.activity AS src, b.activity AS dst, epoch(b.ts - a.ts) AS d
        FROM e a JOIN e b ON a.case_id = b.case_id AND b.rn > a.rn AND b.rn <= a.rn + {MAX_SPAN}{where}""",
                 list(pair) if pair is not None else [])
    conn.execute("""CREATE OR REPLACE TEMP TABLE tp_profile AS
        SELECT src, dst, count(*) AS n, avg(d) AS mean, stddev_samp(d) AS std
        FROM tp_pairs GROUP BY 1, 2 HAVING count(*) >= ? AND stddev_samp(d) > 0""", [min_occurrences])
    conn.execute("""CREATE OR REPLACE TEMP TABLE tp_dev AS
        SELECT p.case_id, p.src, p.dst, p.d, f.mean, (p.d - f.mean) / f.std AS z
        FROM tp_pairs p JOIN tp_profile f USING (src, dst)
        WHERE abs(p.d - f.mean) > ? * f.std""", [float(zeta)])


def _case_deviations(conn, direction: str = "all") -> None:
    where = {"all": "", "slower": "WHERE z > 0", "faster": "WHERE z < 0"}[direction]
    conn.execute(f"""CREATE TEMP TABLE tp_case_evidence AS
        SELECT case_id, count(*) OVER (PARTITION BY case_id) AS deviations, src, dst, d, mean, z
        FROM tp_dev {where}
        QUALIFY row_number() OVER (PARTITION BY case_id ORDER BY abs(z) DESC, src, dst, d) = 1""")


def _format_case(row) -> dict[str, Any]:
    key, count, source, destination, seconds, mean, z = row
    return {"case_id": key, "deviations": count,
            "strongest": {"from": source, "to": destination,
                          "seconds": round(seconds, 3), "mean_seconds": round(mean, 3),
                          "standard_deviations": round(abs(z), 2)}}


def temporal_profile(log_id: str, case_type: str | None = None, *, zeta: float = DEFAULT_ZETA,
                     min_occurrences: int = DEFAULT_MIN_OCCURRENCES,
                     cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    _validate(zeta, min_occurrences)

    def run(conn):
        _prepare(conn, zeta, min_occurrences)
        cases, pairs_checked = conn.execute(
            "SELECT (SELECT count(DISTINCT case_id) FROM ce), (SELECT count(*) FROM tp_pairs p "
            "JOIN tp_profile f USING (src, dst))").fetchone()
        profile_size = conn.execute("SELECT count(*) FROM tp_profile").fetchone()[0]
        pairs = conn.execute("""SELECT f.src, f.dst, f.n, f.mean, f.std, count(v.case_id) AS dev,
                count(v.case_id) FILTER (WHERE v.z > 0) AS slower
            FROM tp_profile f LEFT JOIN tp_dev v USING (src, dst)
            GROUP BY 1, 2, 3, 4, 5 ORDER BY dev DESC, f.n DESC, f.src, f.dst LIMIT ?""", [TOP_PAIRS]).fetchall()
        _case_deviations(conn)
        deviating_cases = conn.execute("SELECT count(*) FROM tp_case_evidence").fetchone()[0]
        top = conn.execute("""SELECT * FROM tp_case_evidence
            ORDER BY deviations DESC, abs(z) DESC, case_id LIMIT ?""", [TOP_CASES]).fetchall()
        return {"cases": cases, "pairs_checked": pairs_checked, "profile_pairs": profile_size,
                "pairs": pairs, "deviating_cases": deviating_cases, "top": top}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    cases = data.pop("cases")
    out_pairs = [{"from": s, "to": t, "occurrences": n, "mean_seconds": round(m, 3), "std_seconds": round(sd, 3),
                  "allowed_seconds": [round(max(0.0, m - zeta * sd), 3), round(m + zeta * sd, 3)],
                  "deviations": dev, "slower": slower, "faster": dev - slower,
                  "deviation_share": round(dev / n, 4)} for s, t, n, m, sd, dev, slower in data.pop("pairs")]
    out_cases = [_format_case(row) for row in data.pop("top")]
    deviating = data.pop("deviating_cases")
    return {"format": FORMAT, "log_id": log_id, **data, "zeta": float(zeta), "min_occurrences": min_occurrences,
            "max_span_events": MAX_SPAN, "cases": cases, "deviating_cases": deviating,
            "deviating_case_share": round(deviating / cases, 4) if cases else None,
            "pairs": out_pairs, "top_cases": out_cases,
            "method": ("for every ordered pair of activities in a case (up to "
                       f"{MAX_SPAN} events apart), the mean and standard deviation of the time between them; an "
                       "occurrence deviates when it is more than zeta standard deviations from the mean"),
            "caveat": ("The profile is learned from the same cases it checks. Deviations are unusual timings to "
                       "review, not errors; pairs seen fewer than min_occurrences times or always with the same "
                       "time are not checked.")}


def temporal_cases(log_id: str, case_type: str | None = None, *, zeta: float = DEFAULT_ZETA,
                  min_occurrences: int = DEFAULT_MIN_OCCURRENCES, source: str | None = None,
                  destination: str | None = None, direction: str = "all", after: str | None = None,
                  limit: int = 50, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    _validate(zeta, min_occurrences)
    if (source is None) != (destination is None) or any(
            not isinstance(value, str) or not value or len(value) > 200
            for value in (source, destination) if value is not None):
        raise EventLogWorkspaceError("a timing pair needs both exact activity labels")
    if not isinstance(direction, str) or direction not in ("all", "slower", "faster"):
        raise EventLogWorkspaceError("direction must be all, slower or faster")
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise EventLogWorkspaceError("page size must be a whole number from 1 to 100")

    def run(conn):
        _prepare(conn, zeta, min_occurrences, (source, destination) if source is not None else None)
        _case_deviations(conn, direction)
        total, occurrences = conn.execute("SELECT count(*), coalesce(sum(deviations), 0) FROM tp_case_evidence").fetchone()
        where, args = (" WHERE case_id > ?", [after]) if after is not None else ("", [])
        rows = conn.execute("SELECT * FROM tp_case_evidence" + where + " ORDER BY case_id LIMIT ?",
                            [*args, limit + 1]).fetchall()
        return {"deviating_cases": total, "deviations": occurrences, "direction": direction,
                "next_after": rows[limit-1][0] if len(rows) > limit else None,
                "cases": [{**_format_case(row), "direction": "slower" if row[-1] > 0 else "faster"}
                          for row in rows[:limit]]}

    return workspace._query_filtered(log_id, case_type, cohort, run)


__all__ = ["temporal_profile", "temporal_cases", "FORMAT"]
