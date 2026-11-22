"""Notice when an imported or appended file was already loaded before.

Every import and appended batch stores the SHA-256 of the uploaded bytes in its quality report. After a
new batch is stored, its digest is looked up among the other stored reports, so the owner learns that
"this exact file was already imported as 'Invoices Q1'" instead of silently keeping two copies. The
check only reads the owner's own local reports, never blocks or changes the import, and matches whole
files byte for byte: a re-export with one changed row is a different file.
"""
from __future__ import annotations

from typing import Any

MAX_MATCHES = 10


def matches(conn, log_id: str, batch: int) -> dict[str, Any]:
    """Earlier batches, in any saved log, whose uploaded file had the same bytes as this batch's file."""
    try:
        row = conn.execute("SELECT json_extract_string(report, '$.source_sha256') FROM log_quality "
                           "WHERE log_id = ? AND batch = ?", [log_id, batch]).fetchone()
        digest = row[0] if row else None
        if not digest:
            return {}
        rows = conn.execute("""SELECT q.log_id, l.name, q.batch, q.created_at FROM log_quality q
            JOIN logs l ON l.log_id = q.log_id
            WHERE json_extract_string(q.report, '$.source_sha256') = ? AND NOT (q.log_id = ? AND q.batch = ?)
            ORDER BY q.created_at, q.log_id, q.batch LIMIT ?""", [digest, log_id, batch, MAX_MATCHES]).fetchall()
    except Exception:
        return {}        # a notice, never a reason to fail an import that already committed
    if not rows:
        return {}
    return {"same_source_as": [{"log_id": other, "name": name, "batch": b, "this_log": other == log_id,
                                "loaded_at": at.isoformat() + "Z"} for other, name, b, at in rows]}


__all__ = ["matches", "MAX_MATCHES"]
