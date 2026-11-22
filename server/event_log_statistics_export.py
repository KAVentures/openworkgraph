"""Activity and resource statistics of the selection as spreadsheet-safe CSV.

The dashboard lists the most frequent activities; for review outside the tool an analyst wants all of
them, with the figures other process-mining tools put in their statistics export: how often each
activity happens and in how many cases, how often it repeats, which share of cases it starts or ends,
and how long cases wait before it. The resource table gives the same view per resource. Both follow
the case filter, the applied activity mapping and hidden activities, like every case-centric result.
Nothing is stored.
"""
from __future__ import annotations

import csv
import io
from typing import Any

from . import event_log_workspace as workspace
from .event_log_variant_export import _cell
from .event_log_workspace import EventLogWorkspaceError

MAX_ROWS = 50_000
NO_RESOURCE = "(no resource)"

_STEPS = """CREATE OR REPLACE TEMP TABLE st AS SELECT case_id, activity, ts, resource,
    epoch(ts - lag(ts) OVER w) AS wait,
    row_number() OVER w AS pos, count(*) OVER (PARTITION BY case_id) AS n
    FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)"""


def _iso(value) -> str:
    return value.isoformat() + "Z" if value is not None else ""


def _write(header: list[str], rows: list[list[Any]]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return out.getvalue()


def activities_csv(log_id: str, case_type: str | None = None, *,
                   cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    def run(conn):
        conn.execute(_STEPS)
        total = conn.execute("SELECT count(DISTINCT case_id) FROM st").fetchone()[0]
        rows = conn.execute(f"""WITH per_case AS (
              SELECT activity, case_id, count(*) AS k FROM st GROUP BY 1, 2)
            SELECT s.activity, count(*) AS events, count(DISTINCT s.case_id) AS cases,
                   (SELECT count(*) FROM per_case p WHERE p.activity = s.activity AND p.k > 1) AS repeated,
                   count(DISTINCT s.resource) FILTER (WHERE s.resource <> '') AS resources,
                   count(*) FILTER (WHERE s.pos = 1) AS starts, count(*) FILTER (WHERE s.pos = s.n) AS ends,
                   quantile_cont(s.wait, 0.5), quantile_cont(s.wait, 0.9), min(s.ts), max(s.ts)
            FROM st s GROUP BY s.activity ORDER BY events DESC, s.activity LIMIT {MAX_ROWS + 1}""").fetchall()
        distinct = conn.execute("SELECT count(DISTINCT activity) FROM st").fetchone()[0]
        return {"rows": rows, "total": total, "distinct": distinct}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    total = data["total"]
    if not total:
        raise EventLogWorkspaceError("no cases in this selection")
    body = [[_cell(a), events, cases, round(cases / total, 6), round(events / cases, 4), repeated, resources,
             round(starts / total, 6), round(ends / total, 6), med, p90, _iso(first), _iso(last)]
            for a, events, cases, repeated, resources, starts, ends, med, p90, first, last in data["rows"][:MAX_ROWS]]
    text = _write(["activity", "events", "cases", "case_share", "events_per_case", "cases_with_repeats",
                   "resources", "starts_case_share", "ends_case_share", "median_wait_before_seconds",
                   "p90_wait_before_seconds", "first_at", "last_at"], body)
    return text, {"cases": total, "rows": data["distinct"], "written": len(body),
                  "truncated": len(data["rows"]) > MAX_ROWS}


def resources_csv(log_id: str, case_type: str | None = None, *,
                  cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    def run(conn):
        conn.execute(_STEPS)
        total_events, total = conn.execute("SELECT count(*), count(DISTINCT case_id) FROM st").fetchone()
        rows = conn.execute(f"""SELECT resource, count(*) AS events, count(DISTINCT case_id), count(DISTINCT activity),
                   mode(activity), quantile_cont(wait, 0.5), min(ts), max(ts)
            FROM st GROUP BY resource ORDER BY events DESC, resource LIMIT {MAX_ROWS + 1}""").fetchall()
        distinct = conn.execute("SELECT count(DISTINCT resource) FROM st").fetchone()[0]
        return {"rows": rows, "total": total, "events": total_events, "distinct": distinct}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    total, events_total = data["total"], data["events"]
    if not total:
        raise EventLogWorkspaceError("no cases in this selection")
    body = [[_cell(r) if r else NO_RESOURCE, events, round(events / events_total, 6), cases, round(cases / total, 6),
             activities, _cell(top), med, _iso(first), _iso(last)]
            for r, events, cases, activities, top, med, first, last in data["rows"][:MAX_ROWS]]
    text = _write(["resource", "events", "event_share", "cases", "case_share", "activities", "most_frequent_activity",
                   "median_wait_before_seconds", "first_at", "last_at"], body)
    return text, {"cases": total, "rows": data["distinct"], "written": len(body),
                  "truncated": len(data["rows"]) > MAX_ROWS}


__all__ = ["activities_csv", "resources_csv", "MAX_ROWS"]
