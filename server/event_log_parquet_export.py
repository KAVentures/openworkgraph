"""Write a saved log's events as an Apache Parquet file.

Parquet is how notebooks, DuckDB, Spark, PM4Py (``pandas.read_parquet``), Power BI and cloud
warehouses take event data in bulk. The file holds exactly the rows of the CSV (exact
interchange) export, one row per event under the chosen case notion, with real column types:

* ``case_id`` and ``activity``: strings;
* ``timestamp``: a UTC-adjusted timestamp with microsecond precision (``TIMESTAMPTZ``), not text;
* ``resource``: a string, null where the event has no resource.

The CSV export is streamed to a temporary file next to the output and converted by DuckDB, so a
large log is never held in memory. The workspace's own Parquet import reads the file back to the
same events.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-parquet-export.v1"


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def write(log_id: str, path: Path, case_type: str | None = None) -> dict[str, Any]:
    staging = path.with_name(path.stem + "-staging.csv")
    try:
        with open(staging, "w", encoding="utf-8", newline="") as handle:
            workspace.export_log(log_id, "csv", handle, case_type)
        conn = duckdb.connect(":memory:")
        try:
            conn.execute("SET TimeZone='UTC'")
            conn.execute(
                f"""COPY (SELECT case_id, activity, CAST("timestamp" AS TIMESTAMPTZ) AS "timestamp",
                                 NULLIF(resource, '') AS resource
                          FROM read_csv({_literal(staging)}, header=true, all_varchar=true, delim=',',
                                        quote='"', escape='"', strict_mode=false,
                                        columns={{'case_id': 'VARCHAR', 'activity': 'VARCHAR',
                                                  'timestamp': 'VARCHAR', 'resource': 'VARCHAR'}}))
                    TO {_literal(path)} (FORMAT parquet, COMPRESSION zstd)""")
            rows = conn.execute(f"SELECT count(*) FROM read_parquet({_literal(path)})").fetchone()[0]
        finally:
            conn.close()
    finally:
        staging.unlink(missing_ok=True)
    return {"format": FORMAT, "rows": rows}


__all__ = ["write", "FORMAT"]
