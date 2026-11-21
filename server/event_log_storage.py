"""How much of this computer's disk the saved event logs use.

Everything stays on this computer, so the owner should be able to see what it costs. The workspace is one
local database file (plus its write-ahead log). DuckDB does not report bytes per log, so each log's share
is estimated from the rows it owns (events, event-object links, objects and attribute values) as a part of
all such rows. The estimate says roughly which logs to delete or archive first; it is not an exact size.
"""
from __future__ import annotations

import shutil
from typing import Any

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event_log_storage.v1"
_ROW_TABLES = ("events", "event_objects", "objects", "object_attribute_values")


def _file_bytes(path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def storage() -> dict[str, Any]:
    path = workspace.database_path()
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            logs = conn.execute("""SELECT log_id, name, imported_at, events, objects FROM logs
                ORDER BY imported_at DESC""").fetchall()
            rows: dict[str, int] = {}
            for table in _ROW_TABLES:
                for log_id, n in conn.execute(f"SELECT log_id, count(*) FROM {table} GROUP BY log_id").fetchall():
                    rows[log_id] = rows.get(log_id, 0) + n
        finally:
            conn.close()
    database = _file_bytes(path)
    wal = _file_bytes(path.with_name(path.name + ".wal"))
    total_rows = sum(rows.values()) or 1
    try:
        free = shutil.disk_usage(path.parent).free
    except OSError:
        free = None
    items = []
    for log_id, name, imported_at, events, objects in logs:
        share = rows.get(log_id, 0) / total_rows
        items.append({"log_id": log_id, "name": name, "imported_at": imported_at.isoformat() + "Z", "events": events,
                      "objects": objects, "rows": rows.get(log_id, 0), "share": round(share, 4),
                      "estimated_bytes": int(round(database * share))})
    items.sort(key=lambda x: (-x["rows"], x["name"]))
    return {"format": FORMAT, "logs": items, "log_count": len(items), "database_bytes": database,
            "write_ahead_log_bytes": wal, "free_disk_bytes": free, "stored_on_this_computer_only": True,
            "estimate": ("Each log's size is estimated from the rows it owns (events, links, objects and attribute "
                         "values) as a share of the database file; deleting a log frees about that much once "
                         "the database is compacted.")}


__all__ = ["storage", "FORMAT"]
