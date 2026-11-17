"""Which case attributes a log has, and their common values: suggestions for case filters.

Case attributes come from an OCEL import (object attributes) or a joined case
table. For each attribute name of the chosen case notion this lists how many
cases carry it, whether its values are numbers (then the minimum and maximum),
and its most frequent values. It only describes the log; it suggests filter
inputs and stores nothing.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-case-attribute-values.v1"
MAX_NAMES = 100
TOP_VALUES = 20
MAX_VALUE = 100


def values(log_id: str, case_type: str | None = None) -> dict[str, Any]:
    def run(conn):
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE cav AS
            SELECT o.object_id AS case_id, kv.key AS k, kv.value AS v, try_cast(kv.value AS DOUBLE) AS x
            FROM objects o JOIN (SELECT DISTINCT case_id FROM ce) c ON c.case_id = o.object_id,
              unnest(map_entries(json_transform(o.attributes, '"MAP(VARCHAR, VARCHAR)"'))) AS m(kv)
            WHERE o.log_id = ?""", [log_id])
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        names = conn.execute(f"""SELECT k, count(DISTINCT case_id), count(*), count(x), count(DISTINCT v), min(x), max(x)
            FROM cav GROUP BY k ORDER BY count(DISTINCT case_id) DESC, k LIMIT {MAX_NAMES + 1}""").fetchall()
        top = conn.execute(f"""SELECT k, v, n FROM (
              SELECT k, v, count(DISTINCT case_id) AS n,
                     row_number() OVER (PARTITION BY k ORDER BY count(DISTINCT case_id) DESC, v) AS r
              FROM cav WHERE length(v) <= {MAX_VALUE} GROUP BY k, v) WHERE r <= {TOP_VALUES}""").fetchall()
        return {"cases": cases, "names": names, "top": top}

    data = workspace._query(log_id, case_type, run)
    by_name: dict[str, list[dict[str, Any]]] = {}
    for k, v, n in data["top"]:
        by_name.setdefault(k, []).append({"value": v, "cases": n})
    attributes = []
    for k, with_cases, rows, numeric_rows, distinct, lo, hi in data["names"][:MAX_NAMES]:
        numeric = rows > 0 and numeric_rows >= 0.9 * rows
        attributes.append({
            "name": k, "cases": with_cases, "distinct_values": distinct, "numeric": numeric,
            **({"min": lo, "max": hi} if numeric else {}),
            "top_values": sorted(by_name.get(k, []), key=lambda t: (-t["cases"], t["value"])),
        })
    return {"format": FORMAT, "case_notion": data["case_notion"], "cases": data["cases"],
            "attributes": attributes, "names_truncated": len(data["names"]) > MAX_NAMES,
            "interpretation": "Case attributes as recorded on the cases; suggestions for filters, not validated categories."}
