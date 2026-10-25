"""Review, release or discard rejected CSV rows kept with a log (#370 R6 quarantine decisions).

Rows are kept only when the user asks for it at import or append (``keep_rejected``): the
mapped fields (case, activity, time text, resource, kept attributes) of up to
``MAX_QUARANTINED_ROWS`` rejected rows per batch, with the reasons they were rejected. They
are stored on this computer next to the log and deleted with it.

A *release* applies one explicit decision — the UTC offset to assume for times written without
one — and appends the rows that become valid as a new batch through the normal append path,
so duplicates are skipped and the batch gets its own quality report recording the decision.
Rows that still fail stay quarantined. A *discard* deletes rows. Both decisions are recorded.
Nothing is released automatically, and no other value in a row is changed.
"""
from __future__ import annotations

import csv
from datetime import datetime, timezone
import io
import json
from typing import Any

from . import event_log_workspace as workspace

MAX_PAGE = 100
REVIEW_NOTE = ("Rejected rows are kept only because this was chosen at import or append. They hold the mapped "
               "values as supplied and are deleted with the log. Releasing assumes the chosen UTC offset for "
               "times without one; it does not verify that the assumption is right.")


def _batch(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise workspace.EventLogWorkspaceError("batch must be a non-negative whole number")
    return value


def _decisions(conn, log_id: str) -> list[dict[str, Any]]:
    return [{"decided_at": at.isoformat() + "Z", **json.loads(d)} for at, d in conn.execute(
        "SELECT decided_at, decision FROM quarantine_decisions WHERE log_id = ? ORDER BY decided_at", [log_id]).fetchall()]


def _record(conn, log_id: str, decision: dict[str, Any]) -> None:
    conn.execute("INSERT INTO quarantine_decisions VALUES (?, ?, ?)",
                 [log_id, datetime.now(timezone.utc).replace(tzinfo=None), json.dumps(decision, sort_keys=True)])


def quarantine(log_id: str, batch: int | None = None, after: int = 0, limit: int = 50) -> dict[str, Any]:
    batch = _batch(batch)
    if not 1 <= int(limit) <= MAX_PAGE or int(after) < 0:
        raise workspace.EventLogWorkspaceError(f"limit must be 1–{MAX_PAGE} and after must not be negative")
    scope, params = ("AND batch = ?", [batch]) if batch is not None else ("", [])
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            batches = [{"batch": b, "rows": n} for b, n in conn.execute(
                "SELECT batch, count(*) FROM quarantine WHERE log_id = ? GROUP BY batch ORDER BY batch", [log_id]).fetchall()]
            reasons = dict(conn.execute(f"""SELECT r, count(*) FROM (SELECT unnest(from_json(reasons, '["VARCHAR"]')) AS r
                FROM quarantine WHERE log_id = ? {scope}) GROUP BY r ORDER BY r""", [log_id, *params]).fetchall())
            rows = conn.execute(f"""SELECT batch, row_n, reasons, row FROM quarantine WHERE log_id = ? {scope}
                AND (batch * 1000000000 + row_n) > ? ORDER BY batch, row_n LIMIT ?""",
                                [log_id, *params, int(after), int(limit) + 1]).fetchall()
            decisions = _decisions(conn, log_id)
        finally:
            conn.close()
    page = [{"batch": b, "row": n, "position": b * 1_000_000_000 + n, "reasons": json.loads(r), **json.loads(v)}
            for b, n, r, v in rows[:int(limit)]]
    return {"batches": batches, "rows_kept": sum(b["rows"] for b in batches), "reasons": reasons,
            "rows": page, "next_after": page[-1]["position"] if len(rows) > int(limit) else None,
            "decisions": decisions, "max_rows_per_batch": workspace.MAX_QUARANTINED_ROWS, "note": REVIEW_NOTE}


def _rows(conn, log_id: str, batch: int | None) -> list[tuple[int, int, dict[str, Any]]]:
    scope, params = ("AND batch = ?", [batch]) if batch is not None else ("", [])
    return [(b, n, json.loads(v)) for b, n, v in conn.execute(
        f"SELECT batch, row_n, row FROM quarantine WHERE log_id = ? {scope} ORDER BY batch, row_n",
        [log_id, *params]).fetchall()]


def release(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"naive_timestamp_offset", "batch"}:
        raise workspace.EventLogWorkspaceError("a release accepts naive_timestamp_offset and an optional batch")
    batch = _batch(body.get("batch"))
    try:
        offset = workspace.naive_offset({"naive_timestamp_offset": body.get("naive_timestamp_offset") or ""})
    except workspace.EventLogFormatError as exc:
        raise workspace.EventLogWorkspaceError(str(exc)) from None
    if not offset:
        raise workspace.EventLogWorkspaceError("choose the UTC offset to assume for times written without one")
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            rows = _rows(conn, log_id, batch)
            if not rows:
                raise workspace.EventLogWorkspaceError("no rejected rows are kept for this log or batch")
            # Which kept rows become valid under the decision (the same parsing rules as an import).
            conn.execute("CREATE OR REPLACE TEMP TABLE qrel (batch INTEGER, row_n BIGINT, c VARCHAR, a VARCHAR, t VARCHAR)")
            conn.executemany("INSERT INTO qrel VALUES (?, ?, ?, ?, ?)",
                             [(b, n, v["case"], v["activity"], v["time"]) for b, n, v in rows])
            valid = {(b, n) for b, n in conn.execute(f"""SELECT batch, row_n FROM qrel WHERE trim(c) <> ''
                AND trim(a) <> '' AND ({workspace.timestamp_sql('t', offset)}) IS NOT NULL""").fetchall()}
        finally:
            conn.close()
        decision = {"kind": "release", "naive_timestamp_offset": offset, "batch": batch,
                    "rows_considered": len(rows), "rows_released": len(valid)}
        result: dict[str, Any] = {}
        if valid:
            attributes = sorted({k for b, n, v in rows if (b, n) in valid for k in v["attributes"]})
            attributes = [k for k in attributes if k not in {"case", "activity", "time", "resource"}]
            text = io.StringIO()
            writer = csv.writer(text)
            writer.writerow(["case", "activity", "time", "resource", *attributes])
            for b, n, v in rows:
                if (b, n) in valid:
                    writer.writerow([v["case"], v["activity"], v["time"], v["resource"],
                                     *[v["attributes"].get(k, "") for k in attributes]])
            mapping = {"case_column": "case", "activity_column": "activity", "timestamp_column": "time",
                       "resource_column": "resource", "attribute_columns": attributes,
                       "naive_timestamp_offset": offset}
            result = workspace.append_log(log_id, io.BytesIO(text.getvalue().encode()), source_format="csv",
                                          mapping=mapping, kind="quarantine_release")
            decision.update(appended_batch=result["batch"], events_added=result["added_events"],
                            duplicates_skipped=result["duplicate_events_skipped"])
        conn = workspace._connect()
        try:
            # The append above committed; removing the released rows is a second step. If it is
            # interrupted, releasing again appends nothing new because duplicates are skipped.
            conn.execute("BEGIN")
            if valid:
                conn.executemany("DELETE FROM quarantine WHERE log_id = ? AND batch = ? AND row_n = ?",
                                 [(log_id, b, n) for b, n in sorted(valid)])
            decision["rows_still_kept"] = conn.execute("SELECT count(*) FROM quarantine WHERE log_id = ?",
                                                       [log_id]).fetchone()[0]
            _record(conn, log_id, decision)
            conn.execute("COMMIT")
        finally:
            conn.close()
    return {"decision": decision, "append": result or None, **workspace.DESCRIPTIVE_ONLY}


def discard(log_id: str, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict) or set(body) - {"batch"}:
        raise workspace.EventLogWorkspaceError("a discard accepts only an optional batch")
    batch = _batch(body.get("batch"))
    scope, params = ("AND batch = ?", [batch]) if batch is not None else ("", [])
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            conn.execute("BEGIN")
            removed = conn.execute(f"DELETE FROM quarantine WHERE log_id = ? {scope}", [log_id, *params]).fetchone()[0]
            decision = {"kind": "discard", "batch": batch, "rows_discarded": removed}
            _record(conn, log_id, decision)
            conn.execute("COMMIT")
        finally:
            conn.close()
    return {"decision": decision}


__all__ = ["discard", "quarantine", "release"]
