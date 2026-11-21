"""Breakdown table: case KPIs by one or two dimensions, as a pivot (a small process cube).

Process mining suites let analysts slice KPIs by attributes in a table (Celonis OLAP tables, UiPath
Process Mining's dashboards, the process cubes of Bolt, Ramezani and van der Aalst). This module
groups the cases in the selection by up to two dimensions and gives, for every cell:

* the number of cases and their share of the selection;
* the median and 90th-percentile case duration (first to last recorded event);
* events per case and the rework rate (cases that repeat an activity);
* optionally, the share of cases that do a chosen activity.

A dimension is a case attribute (``attribute:<name>``, as the case filter reads it), an event
attribute (``event_attribute:<name>``, the first non-empty value recorded in the case), the month or
weekday the case started, or its first or last activity. Only the most common
values are shown on each side; the rest are grouped as ``(other)``, and cases without a value as
``(missing)``. Cells with few cases are flagged, as their figures move a lot with one case. The
table describes the recorded cases; a difference between cells is not a cause.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_breakdown.v1"
BUILT_IN = {"start_month": "month the case started", "start_weekday": "weekday the case started",
            "start_activity": "first activity", "end_activity": "last activity",
            "duration_band": "case duration", "rework": "rework"}
# Duration bands are computed as sortable codes and shown with these labels.
DURATION_BANDS = {"1": "under 1 hour", "2": "1 to 24 hours", "3": "1 to 7 days", "4": "7 to 30 days", "5": "30 days or more"}
TAGGED, NOT_TAGGED = "tagged", "not tagged"
MAX_ROWS, MAX_COLUMNS = 30, 12
SMALL_CELL = 5
OTHER, MISSING = "(other)", "(missing)"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _dimension(raw: Any, name: str) -> tuple[str, str | None]:
    if not isinstance(raw, str) or not raw:
        raise EventLogWorkspaceError(f"choose a {name} dimension")
    if raw in BUILT_IN:
        return raw, None
    for prefix in ("attribute:", "event_attribute:", "tag:"):
        if raw.startswith(prefix) and 0 < len(raw) - len(prefix) <= 200:
            return prefix[:-1], raw[len(prefix):]
    raise EventLogWorkspaceError(f"{name} must be one of {', '.join(BUILT_IN)}, attribute:<name>, "
                                 "event_attribute:<name> or tag:<name>")


def _expr(kind: str) -> str:
    return {"start_month": "strftime(cs.started, '%Y-%m')",
            "start_weekday": "CAST(isodow(cs.started) AS VARCHAR)",
            "start_activity": "cs.first_activity", "end_activity": "cs.last_activity",
            "duration_band": """CASE WHEN cs.duration < 3600 THEN '1' WHEN cs.duration < 86400 THEN '2'
                WHEN cs.duration < 604800 THEN '3' WHEN cs.duration < 2592000 THEN '4' ELSE '5' END""",
            "rework": "CASE WHEN cs.rework THEN 'repeats an activity' ELSE 'no repeats' END"}[kind]


def breakdown(log_id: str, rows: str, columns: str | None = None, case_type: str | None = None, *,
              with_activity: str | None = None, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    row_kind, row_attr = _dimension(rows, "rows")
    col_kind, col_attr = _dimension(columns, "columns") if columns not in (None, "") else (None, None)
    if columns not in (None, "") and columns == rows:
        raise EventLogWorkspaceError("choose two different dimensions")
    if with_activity is not None and (not isinstance(with_activity, str) or not with_activity or len(with_activity) > 500):
        raise EventLogWorkspaceError("with_activity must name an activity")

    def run(conn):
        conn.execute("""CREATE OR REPLACE TEMP TABLE cs AS SELECT case_id, min(ts) AS started,
              epoch(max(ts) - min(ts)) AS duration, count(*) AS events,
              count(*) > count(DISTINCT activity) AS rework,
              arg_min(activity, (ts, event_id)) AS first_activity, arg_max(activity, (ts, event_id)) AS last_activity,
              bool_or(activity = ?) AS did FROM ce GROUP BY case_id""", [with_activity or ""])
        total = conn.execute("SELECT count(*) FROM cs").fetchone()[0]
        if not total:
            raise EventLogWorkspaceError("no cases in this selection")
        if with_activity is not None and not conn.execute("SELECT bool_or(did) FROM cs").fetchone()[0]:
            raise EventLogWorkspaceError("no case in this selection does " + with_activity)
        dims = []
        for slot, kind, attr in (("r", row_kind, row_attr), ("c", col_kind, col_attr)):
            if kind is None:
                dims.append("NULL")
                continue
            if kind == "attribute":
                # The case's own attribute, as the case filter reads it.
                conn.execute(f"""CREATE OR REPLACE TEMP TABLE dim_{slot} AS SELECT object_id AS case_id,
                      map_extract_value(json_transform(attributes, '"MAP(VARCHAR, VARCHAR)"'), ?) AS value
                    FROM objects WHERE log_id = ?""", [attr, log_id])
                dims.append(f"(SELECT nullif(value, '') FROM dim_{slot} d WHERE d.case_id = cs.case_id)")
            elif kind == "tag":
                # Cases the analyst tagged with this tag, and the rest.
                conn.execute(f"""CREATE OR REPLACE TEMP TABLE dim_{slot} AS SELECT DISTINCT case_id FROM case_tags
                    WHERE log_id = ? AND tag = ?""", [log_id, attr])
                dims.append(f"""CASE WHEN cs.case_id IN (SELECT case_id FROM dim_{slot}) THEN '{TAGGED}'
                    ELSE '{NOT_TAGGED}' END""")
            elif kind == "event_attribute":
                # The first non-empty value recorded on the case's events.
                conn.execute(f"""CREATE OR REPLACE TEMP TABLE dim_{slot} AS SELECT case_id,
                      arg_min(v, (ts, event_id)) AS value FROM (
                        SELECT ce.case_id, ce.ts, ce.event_id,
                               CASE WHEN j.type = 'VARCHAR' THEN j.value->>'$' ELSE j.value::VARCHAR END AS v
                        FROM ce, json_each(ce.attributes) j WHERE j.key = ? AND j.type <> 'NULL')
                    WHERE v <> '' GROUP BY case_id""", [attr])
                dims.append(f"(SELECT value FROM dim_{slot} d WHERE d.case_id = cs.case_id)")
            else:
                dims.append(_expr(kind))
        cells = conn.execute(f"""SELECT coalesce({dims[0]}, ?) AS r, coalesce({dims[1]}, ?) AS c, case_id, duration,
              events, rework, did FROM cs""", [MISSING, MISSING]).fetchall()
        return {"cells": cells, "total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    total = data["total"]
    raw = data["cells"]

    def label(kind: str | None, value: str) -> str:
        if kind == "start_weekday" and value != MISSING:
            return WEEKDAYS[int(value) - 1]
        if kind == "duration_band" and value != MISSING:
            return DURATION_BANDS[value]
        return value

    def keep(index: int, limit: int, kind: str | None) -> tuple[dict[str, str], list[str]]:
        """Map each raw value to its shown label; less common (or, for time, older) values become (other)."""
        counts: dict[str, int] = {}
        for cell in raw:
            counts[cell[index]] = counts.get(cell[index], 0) + 1
        values = [v for v in counts if v != MISSING]
        if kind in ("start_month", "start_weekday", "duration_band"):
            shown = sorted(values)[-limit:]
        else:
            shown = sorted(values, key=lambda v: (-counts[v], v))[:limit]
        mapping = {v: (label(kind, v) if v in shown else OTHER) for v in values}
        if MISSING in counts:
            mapping[MISSING] = MISSING
        order = [label(kind, v) for v in shown] + [x for x in (OTHER, MISSING) if x in mapping.values()]
        return mapping, order

    row_map, order_r = keep(0, MAX_ROWS, row_kind)
    col_map, order_c = keep(1, MAX_COLUMNS, col_kind) if col_kind else ({MISSING: "all cases"}, ["all cases"])
    groups: dict[tuple[str, str], list] = {}
    for r, c, _cid, duration, events, rework, did in raw:
        groups.setdefault((row_map[r], col_map[c]), []).append((duration, events, rework, did))

    def quantile(values: list[float], q: float) -> float | None:
        if not values:
            return None
        s = sorted(values)
        pos = (len(s) - 1) * q
        lo = int(pos)
        hi = min(lo + 1, len(s) - 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    def stats(items: list) -> dict[str, Any]:
        n = len(items)
        durations = [d for d, _, _, _ in items if d is not None]
        out = {"cases": n, "share": round(n / total, 4),
               "median_hours": None if not durations else round(quantile(durations, 0.5) / 3600, 3),
               "p90_hours": None if not durations else round(quantile(durations, 0.9) / 3600, 3),
               "events_per_case": round(sum(e for _, e, _, _ in items) / n, 3),
               "rework_rate": round(sum(1 for _, _, w, _ in items if w) / n, 4),
               "small": n < SMALL_CELL}
        if with_activity is not None:
            out["with_activity_share"] = round(sum(1 for *_, did in items if did) / n, 4)
        return out

    table = [{"row": r, "column": c, **stats(groups[(r, c)])} for r in order_r for c in order_c if (r, c) in groups]
    row_totals = [{"row": r, **stats([x for c in order_c for x in groups.get((r, c), [])])} for r in order_r]
    col_totals = [{"column": c, **stats([x for r in order_r for x in groups.get((r, c), [])])} for c in order_c]
    every = [x for items in groups.values() for x in items]

    def describe(raw_name: str | None, kind: str | None, attr: str | None) -> dict[str, Any] | None:
        if kind is None:
            return None
        label_text = {"attribute": f"case attribute {attr}", "event_attribute": f"event attribute {attr}",
                      "tag": f"tag {attr}"}
        return {"dimension": raw_name, "label": label_text.get(kind) or BUILT_IN[kind]}

    return {
        "format": FORMAT, "log_id": log_id, "case_notion": data.get("case_notion"),
        "rows": describe(rows, row_kind, row_attr), "columns": describe(columns, col_kind, col_attr),
        "row_values": order_r, "column_values": order_c if col_kind else [],
        "with_activity": with_activity, "cases": total,
        "cells": table, "row_totals": row_totals, "column_totals": col_totals if col_kind else [],
        "overall": stats(every),
        "limits": {"rows": MAX_ROWS, "columns": MAX_COLUMNS, "small_cell_cases": SMALL_CELL},
        "method": ("cases grouped by the chosen dimensions; an event attribute uses the first non-empty value recorded "
                   "in the case; less common values (for time, older ones) are grouped as (other)"),
        "caveat": ("Figures describe the recorded cases in each cell. A difference between cells is not a cause, and "
                   "cells with fewer than 5 cases move a lot with one case."),
    }


MAX_DIMENSION_ATTRIBUTES = 100


def dimensions(log_id: str, case_type: str | None = None) -> dict[str, Any]:
    """The dimensions a breakdown can use in this log: built-in ones and the attribute names found."""
    def run(conn):
        events = conn.execute("""SELECT j.key, count(DISTINCT ce.case_id) AS cases FROM ce, json_each(ce.attributes) j
            WHERE j.type <> 'NULL' GROUP BY 1 ORDER BY cases DESC, 1 LIMIT ?""", [MAX_DIMENSION_ATTRIBUTES]).fetchall()
        case = conn.execute("""SELECT j.key, count(*) AS cases FROM objects o, json_each(o.attributes) j
            WHERE o.log_id = ? AND o.object_id IN (SELECT DISTINCT case_id FROM ce) AND j.type <> 'NULL'
            GROUP BY 1 ORDER BY cases DESC, 1 LIMIT ?""", [log_id, MAX_DIMENSION_ATTRIBUTES]).fetchall()
        tags = conn.execute("""SELECT tag, count(DISTINCT case_id) FROM case_tags WHERE log_id = ?
            AND case_id IN (SELECT DISTINCT case_id FROM ce) GROUP BY 1 ORDER BY 1""", [log_id]).fetchall()
        return {"events": events, "case": case, "tags": tags}
    data = workspace._query(log_id, case_type, run)
    options = [{"dimension": k, "label": v} for k, v in BUILT_IN.items()]
    options += [{"dimension": "attribute:" + k, "label": f"case attribute {k}", "cases": n} for k, n in data["case"]]
    options += [{"dimension": "event_attribute:" + k, "label": f"event attribute {k}", "cases": n}
                for k, n in data["events"]]
    options += [{"dimension": "tag:" + t, "label": f"tag {t}", "cases": n} for t, n in data["tags"]]
    return {"format": FORMAT, "log_id": log_id, "dimensions": options}


def _cell(value: Any) -> str:
    """CSV cell safe to open in a spreadsheet: a leading formula character is neutralized."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def breakdown_csv(log_id: str, rows: str, columns: str | None = None, case_type: str | None = None, *,
                  with_activity: str | None = None, cohort: dict[str, Any] | None = None) -> str:
    """The breakdown as CSV, one line per cell, then the row totals, column totals and overall ("All")."""
    import csv
    import io
    r = breakdown(log_id, rows, columns, case_type, with_activity=with_activity, cohort=cohort)
    figures = ["cases", "share", "median_hours", "p90_hours", "events_per_case", "rework_rate"] + (
        ["with_activity_share"] if with_activity is not None else [])
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([r["rows"]["label"], r["columns"]["label"] if r["columns"] else "column", *figures, "fewer_than_5_cases"])
    every = "All"
    lines = [(c["row"], c["column"], c) for c in r["cells"]]
    lines += [(t["row"], every, t) for t in r["row_totals"]] if r["columns"] else []
    lines += [(every, t["column"], t) for t in r["column_totals"]]
    lines.append((every, every, r["overall"]))
    for row, column, figs in lines:
        writer.writerow([_cell(row), _cell(column), *[figs.get(f) for f in figures], str(bool(figs.get("small"))).lower()])
    return out.getvalue()


__all__ = ["breakdown", "breakdown_csv", "dimensions", "FORMAT", "BUILT_IN"]
