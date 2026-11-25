"""Every variant (distinct path) of the selection as spreadsheet-safe CSV.

Process mining tools export the variant list for review outside the tool: one row per distinct
sequence of activities, with how many cases follow it, its share and the running share (so the
reader sees how many variants cover, say, 80% of the cases), and the median and 90th-percentile
case duration. Activities in a path are joined with " > ". At most MAX_VARIANTS rows are written;
the response says when the list was cut.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

MAX_VARIANTS = 10_000
_FORMULA_PREFIX = re.compile(r"^[\s\ufeff]*[=+\-@]")


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    # Spreadsheet software may interpret formulas after leading whitespace or a BOM.
    # Keep the original cell text, but force it to be treated as literal text.
    if text.startswith(("\t", "\r", "\n")) or _FORMULA_PREFIX.match(text):
        return "'" + text
    return text


def variants_csv(log_id: str, case_type: str | None = None, *,
                 cohort: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    def run(conn):
        rows = conn.execute(f"""WITH v AS (
              SELECT case_id, list(activity ORDER BY ts, event_id) AS path, count(*) AS steps,
                     epoch(max(ts) - min(ts)) AS duration FROM ce GROUP BY case_id)
            SELECT path, any_value(steps), count(*) AS cases, quantile_cont(duration, 0.5), quantile_cont(duration, 0.9)
            FROM v GROUP BY path ORDER BY cases DESC, path LIMIT {MAX_VARIANTS + 1}""").fetchall()
        total = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        distinct = conn.execute(f"""SELECT count(DISTINCT p) FROM (SELECT list(activity ORDER BY ts, event_id) AS p FROM ce GROUP BY case_id)""").fetchone()[0]
        return {"rows": rows, "total": total, "distinct": distinct}

    data = workspace._query_filtered(log_id, case_type, cohort, run)
    rows, total = data["rows"], data["total"]
    if not total:
        raise EventLogWorkspaceError("no cases in this selection")
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["rank", "path", "steps", "cases", "share", "cumulative_share", "median_seconds", "p90_seconds"])
    running = 0
    for rank, (path, steps, n, median, p90) in enumerate(rows[:MAX_VARIANTS], start=1):
        running += n
        writer.writerow([rank, _cell(" > ".join(path)), steps, n, round(n / total, 6),
                         round(running / total, 6), median, p90])
    return out.getvalue(), {"cases": total, "variants": data["distinct"], "written": min(len(rows), MAX_VARIANTS),
                            "truncated": len(rows) > MAX_VARIANTS}


__all__ = ["variants_csv", "MAX_VARIANTS"]
