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

from collections import Counter
from hashlib import sha256
from heapq import nsmallest
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


def _read(log_id: str, rows: str, columns: str | None, case_type: str | None,
          with_activity: str | None, cohort: dict[str, Any] | None):
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
        cells = conn.execute(f"""SELECT {dims[0]} AS r, {dims[1]} AS c, case_id, duration,
              events, rework, did FROM cs""").fetchall()
        return {"cells": cells, "total": total}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    return data, (row_kind, row_attr), (col_kind, col_attr)


def _unused_label(preferred: str, fallback: str, occupied: set[str]) -> str:
    if preferred not in occupied:
        return preferred
    candidate, number = fallback, 2
    while candidate in occupied:
        candidate = f"{fallback} {number}"
        number += 1
    return candidate


def _axis(raw: list, index: int, limit: int, kind: str | None):
    """Use separate keys for literal dimension values and report buckets."""
    if kind is None:
        return {None: "all"}, {"all": "all cases"}
    counts = Counter(cell[index] for cell in raw)
    values = [v for v in counts if v is not None]
    if kind in ("start_month", "start_weekday", "duration_band"):
        shown = sorted(values)[-limit:]
    else:
        shown = sorted(values, key=lambda v: (-counts[v], v))[:limit]
    mappings = dict.fromkeys(values, "other")
    labels: dict[str, str] = {}
    for value in shown:
        key = "value:" + sha256(value.encode()).hexdigest()
        mappings[value] = key
        if kind == "start_weekday":
            labels[key] = WEEKDAYS[int(value) - 1]
        elif kind == "duration_band":
            labels[key] = DURATION_BANDS[value]
        else:
            labels[key] = value
    occupied = set(values) | set(labels.values())
    if len(values) > len(shown):
        labels["other"] = _unused_label(OTHER, "(other grouped values)", occupied)
    if None in counts:
        mappings[None] = "missing"
        labels["missing"] = _unused_label(MISSING, "(missing value)", occupied)
    return mappings, labels


def breakdown(log_id: str, rows: str, columns: str | None = None, case_type: str | None = None, *,
              with_activity: str | None = None, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    data, (row_kind, row_attr), (col_kind, col_attr) = _read(log_id, rows, columns, case_type, with_activity, cohort)
    total = data["total"]
    raw = data["cells"]
    row_map, row_labels = _axis(raw, 0, MAX_ROWS, row_kind)
    col_map, col_labels = _axis(raw, 1, MAX_COLUMNS, col_kind)
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

    table = [{"row": row_labels[r], "row_key": r, "column": col_labels[c],
              "column_key": c if col_kind else None, **stats(groups[(r, c)])}
             for r in row_labels for c in col_labels if (r, c) in groups]
    row_totals = [{"row": row_labels[r], "row_key": r,
                   **stats([x for c in col_labels for x in groups.get((r, c), [])])} for r in row_labels]
    col_totals = [{"column": col_labels[c], "column_key": c,
                   **stats([x for r in row_labels for x in groups.get((r, c), [])])} for c in col_labels]
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
        "row_values": list(row_labels.values()), "column_values": list(col_labels.values()) if col_kind else [],
        "total_label": _unused_label("All", "All cases", set(row_labels.values()) | set(col_labels.values())),
        "with_activity": with_activity, "cases": total,
        "cells": table, "row_totals": row_totals, "column_totals": col_totals if col_kind else [],
        "overall": stats(every),
        "limits": {"rows": MAX_ROWS, "columns": MAX_COLUMNS, "small_cell_cases": SMALL_CELL},
        "method": ("cases grouped by the chosen dimensions; an event attribute uses the first non-empty value recorded "
                   "in the case; less common values (for time, older ones) are grouped as (other)"),
        "caveat": ("Figures describe the recorded cases in each cell. A difference between cells is not a cause, and "
                   "cells with fewer than 5 cases move a lot with one case."),
    }


def cell_cases(log_id: str, rows: str, columns: str | None = None, case_type: str | None = None, *,
               selection: dict[str, Any], with_activity: str | None = None,
               cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    """Review the cases contributing to a displayed cell or total."""
    if not isinstance(selection, dict) or selection.keys() - {"row_key", "column_key", "after", "limit"}:
        raise EventLogWorkspaceError("unknown breakdown case setting")
    row, column = selection.get("row_key"), selection.get("column_key")
    if any(key is not None and (not isinstance(key, str) or not key or len(key) > 70) for key in (row, column)):
        raise EventLogWorkspaceError("use the group keys returned by the breakdown")
    if column is not None and not columns:
        raise EventLogWorkspaceError("a column key requires a column dimension")
    after, limit = selection.get("after"), selection.get("limit", 50)
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise EventLogWorkspaceError("invalid case key cursor")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise EventLogWorkspaceError("limit must be a whole number from 1 to 100")
    data, (row_kind, _), (col_kind, _) = _read(log_id, rows, columns, case_type, with_activity, cohort)
    raw = data["cells"]
    row_map, row_labels = _axis(raw, 0, MAX_ROWS, row_kind)
    col_map, col_labels = _axis(raw, 1, MAX_COLUMNS, col_kind)
    if (row is not None and row not in row_labels) or (column is not None and column not in col_labels):
        raise EventLogWorkspaceError("the group is no longer displayed; refresh the breakdown")
    total = 0

    def matching():
        nonlocal total
        for r, c, key, duration, events, rework, did in raw:
            if (row is None or row_map[r] == row) and (column is None or col_map[c] == column):
                total += 1
                if after is None or key > after:
                    yield key, duration, events, rework, did

    page = nsmallest(limit + 1, matching(), key=lambda result: result[0])
    return {"case_notion": data["case_notion"], "total": total,
            "row": row_labels.get(row), "column": col_labels.get(column),
            "cases": [{"case_id": key, "duration_seconds": duration, "events": events, "rework": rework,
                       **({"did_activity": did} if with_activity is not None else {})}
                      for key, duration, events, rework, did in page[:limit]],
            "next_after": page[limit - 1][0] if len(page) > limit else None}


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
    from shared.spreadsheet_review import review_cell

    text = "" if value is None else str(value)
    return review_cell(text)


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
    writer.writerow([_cell(r["rows"]["label"]), _cell(r["columns"]["label"]) if r["columns"] else "column", *figures, "fewer_than_5_cases"])
    every = r["total_label"]
    lines = [(c["row"], c["column"], c) for c in r["cells"]]
    lines += [(t["row"], every, t) for t in r["row_totals"]] if r["columns"] else []
    lines += [(every, t["column"], t) for t in r["column_totals"]]
    lines.append((every, every, r["overall"]))
    for row, column, figs in lines:
        writer.writerow([_cell(row), _cell(column), *[figs.get(f) for f in figures], str(bool(figs.get("small"))).lower()])
    return out.getvalue()


__all__ = ["breakdown", "breakdown_csv", "cell_cases", "dimensions", "FORMAT", "BUILT_IN"]
