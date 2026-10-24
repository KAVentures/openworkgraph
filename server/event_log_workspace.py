"""Local columnar workspace for large business event logs (CSV, XES, OCEL 2.0).

The bounded CSV preview (``process_mining_csv``) stays the quick path for small
exports. This workspace handles multi-million-event logs with DuckDB on this
computer only: logs are imported explicitly by the user, never uploaded, and can
be deleted. Every query takes a *case notion* (an object type); flat logs use the
single object type ``case``, OCEL logs can be analysed per object type.

Derived results describe the supplied log. They are not verified business
workflows, causes, policies or permission to act.
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from typing import IO, TYPE_CHECKING, Any, Iterator

if TYPE_CHECKING:
    import duckdb

from shared.event_log_formats import (
    CASE_TYPE, EventLogFormatError, EventRecord, ObjectRecord, ReadStats,
    read_csv, read_csv_header, read_ocel_json, read_ocel_xml, read_xes, write_csv, write_ocel_json,
    write_ocel_xml, write_xes,
)

from .db import DATA_DIR

MAX_LOGS = 50
MAX_IMPORT_BYTES = 4 * 1024 * 1024 * 1024
_LOG_ID = re.compile(r"^log_[0-9a-f]{16}$")
_NAME = re.compile(r"^[^\x00-\x1f<>]{1,120}$")
_SEP = "\x1f"
_LOCK = threading.RLock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS logs (
  log_id VARCHAR PRIMARY KEY, name VARCHAR NOT NULL, source_format VARCHAR NOT NULL,
  imported_at TIMESTAMP NOT NULL, events BIGINT NOT NULL, objects BIGINT NOT NULL,
  rejected_events BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  log_id VARCHAR NOT NULL, event_id VARCHAR NOT NULL, activity VARCHAR NOT NULL,
  ts TIMESTAMP NOT NULL, resource VARCHAR NOT NULL, attributes VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS objects (
  log_id VARCHAR NOT NULL, object_id VARCHAR NOT NULL, object_type VARCHAR NOT NULL,
  attributes VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS event_objects (
  log_id VARCHAR NOT NULL, event_id VARCHAR NOT NULL, object_id VARCHAR NOT NULL,
  qualifier VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS log_appends (
  log_id VARCHAR NOT NULL, batch INTEGER NOT NULL, appended_at TIMESTAMP NOT NULL, source_format VARCHAR NOT NULL,
  added_events BIGINT NOT NULL, duplicate_events BIGINT NOT NULL, rejected_events BIGINT NOT NULL,
  added_objects BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS saved_analyses (
  log_id VARCHAR NOT NULL, analysis_id VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
  definition VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS signal_rules (
  log_id VARCHAR NOT NULL, rule_id VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
  definition VARCHAR NOT NULL, last_evaluated_at TIMESTAMP, last_state VARCHAR
);
"""

DESCRIPTIVE_ONLY = {
    "derived_from_supplied_log": True,
    "verified_business_workflow": False,
    "causal_claims": False,
    "execution_authorized": False,
}


class EventLogWorkspaceError(ValueError):
    """Safe user-facing error; never echoes log content."""


def database_path() -> Path:
    return DATA_DIR / "event_logs.duckdb"


def _connect() -> duckdb.DuckDBPyConnection:
    try:
        import duckdb
    except ImportError:  # the local app keeps starting; only event-log features are unavailable
        raise EventLogWorkspaceError("event-log workspace requires the duckdb package; reinstall OpenWorkGraph") from None
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(path))
    conn.execute("SET enable_progress_bar = false")
    conn.execute(_SCHEMA)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


def _check_log(conn: duckdb.DuckDBPyConnection, log_id: str) -> dict[str, Any]:
    if not _LOG_ID.fullmatch(str(log_id or "")):
        raise EventLogWorkspaceError("unknown event log")
    row = conn.execute("SELECT log_id, name, source_format, imported_at, events, objects, rejected_events "
                       "FROM logs WHERE log_id = ?", [log_id]).fetchone()
    if not row:
        raise EventLogWorkspaceError("unknown event log")
    return {"log_id": row[0], "name": row[1], "source_format": row[2],
            "imported_at": row[3].isoformat() + "Z", "events": row[4],
            "objects": row[5], "rejected_events": row[6]}


# ---------------------------------------------------------------- import

def _stage(records: Iterator[EventRecord | ObjectRecord], folder: Path) -> tuple[Path, Path, Path]:
    paths = folder / "events.csv", folder / "objects.csv", folder / "links.csv"
    with paths[0].open("w", newline="", encoding="utf-8") as ev, \
            paths[1].open("w", newline="", encoding="utf-8") as ob, \
            paths[2].open("w", newline="", encoding="utf-8") as ln:
        ew, ow, lw = csv.writer(ev), csv.writer(ob), csv.writer(ln)
        for record in records:
            if isinstance(record, ObjectRecord):
                ow.writerow([record.object_id, record.object_type, json.dumps(record.attributes, ensure_ascii=False)])
                continue
            ew.writerow([record.event_id, record.activity,
                         record.timestamp.astimezone(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
                         record.resource, json.dumps(record.attributes, ensure_ascii=False)])
            for object_id, qualifier in record.objects:
                lw.writerow([record.event_id, object_id, qualifier])
    return paths


def _load(conn: duckdb.DuckDBPyConnection, log_id: str, paths: tuple[Path, Path, Path]) -> None:
    opts = "header=false, quote='\"', escape='\"', delim=','"
    if paths[0].stat().st_size:
        conn.execute(f"""INSERT INTO events SELECT ?, c0, c1, c2::TIMESTAMP, coalesce(c3, ''), coalesce(c4, '{{}}') FROM read_csv(?, {opts},
            columns={{'c0':'VARCHAR','c1':'VARCHAR','c2':'VARCHAR','c3':'VARCHAR','c4':'VARCHAR'}})""",
                     [log_id, str(paths[0])])
    if paths[1].stat().st_size:
        conn.execute(f"""INSERT INTO objects SELECT ?, c0, c1, coalesce(c2, '{{}}') FROM read_csv(?, {opts},
            columns={{'c0':'VARCHAR','c1':'VARCHAR','c2':'VARCHAR'}})""", [log_id, str(paths[1])])
    # Validate globally in the columnar engine, preserving streaming XES memory
    # instead of retaining every event ID in a Python set. The caller rolls the
    # whole import back before any ambiguous identifier can reach a case query.
    for table,column in (("events","event_id"),("objects","object_id")):
        if conn.execute(f"SELECT 1 FROM {table} WHERE log_id=? GROUP BY {column} HAVING count(*)>1 LIMIT 1",[log_id]).fetchone():
            raise EventLogFormatError("event and object identifiers must be unique within a log; generated and supplied identifiers cannot collide")
    if paths[2].stat().st_size:
        conn.execute(f"""INSERT INTO event_objects SELECT DISTINCT ?, c0, c1, coalesce(c2, '') FROM read_csv(?, {opts},
            columns={{'c0':'VARCHAR','c1':'VARCHAR','c2':'VARCHAR'}})""", [log_id, str(paths[2])])


def _load_csv_fast(conn: duckdb.DuckDBPyConnection, log_id: str, path: Path, mapping: dict[str, Any]) -> ReadStats:
    """Bulk CSV import in SQL, with the same rules as the streaming reader."""
    delimiter = mapping.get("delimiter") or ","
    if delimiter not in {",", ";", "\t", "|"}:
        raise EventLogFormatError("unsupported delimiter")
    columns = [mapping.get(k) or "" for k in ("case_column", "activity_column", "timestamp_column")]
    resource = mapping.get("resource_column") or ""
    if not all(columns):
        raise EventLogFormatError("case, activity and timestamp columns are required")
    source = f"read_csv(?, header=true, names=?, delim='{delimiter}', all_varchar=true, sample_size=-1)"
    with path.open("r",encoding="utf-8-sig",newline="") as handle:
        original_header=read_csv_header(handle,delimiter)
    header = [row[0] for row in conn.execute(f"DESCRIBE SELECT * FROM {source}", [str(path),original_header]).fetchall()]
    if header!=original_header:
        raise EventLogFormatError("CSV column names must be preserved literally without automatic renaming")
    missing = [name for name in columns + ([resource] if resource else []) if name not in header]
    if missing:
        raise EventLogFormatError("missing required columns: " + ", ".join(sorted(missing))[:300])
    q = lambda name: '"' + name.replace('"', '""') + '"'
    case, activity, stamp = (q(c) for c in columns)
    res = q(resource) if resource else "''"
    conn.execute(f"""CREATE TEMP TABLE staged AS SELECT
          {case} AS case_id, {activity} AS activity,
          CASE WHEN regexp_matches(trim({stamp}), '(Z|[+-][0-9]{{2}}:?[0-9]{{2}})$')
               THEN try_cast(trim({stamp}) AS TIMESTAMPTZ) END AS ts,
          coalesce({res}, '') AS resource,
          row_number() OVER () AS n
        FROM {source}""", [str(path),original_header])
    if conn.execute("""SELECT count(*) FROM staged WHERE length(case_id)>200 OR length(activity)>200
        OR length(resource)>200 OR regexp_matches(concat_ws('',case_id,activity,resource),?)""",
        [r'[\x00-\x1f\x7f-\x9f\x{fffe}\x{ffff}]']).fetchone()[0]:
        raise EventLogFormatError("event-log identifiers and activity labels exceed their bounds or contain control characters")
    total = conn.execute("SELECT count(*) FROM staged").fetchone()[0]
    conn.execute("DELETE FROM staged WHERE case_id IS NULL OR trim(case_id) = '' OR activity IS NULL "
                 "OR trim(activity) = '' OR ts IS NULL")
    kept = conn.execute("SELECT count(*) FROM staged").fetchone()[0]
    conn.execute("""INSERT INTO events SELECT ?, 'e' || n, activity, timezone('UTC', ts), resource, '{}' FROM staged""",
                 [log_id])
    conn.execute("INSERT INTO objects SELECT DISTINCT ?, case_id, ?, '{}' FROM staged", [log_id, CASE_TYPE])
    conn.execute("INSERT INTO event_objects SELECT ?, 'e' || n, case_id, ? FROM staged", [log_id, CASE_TYPE])
    objects = conn.execute("SELECT count(*) FROM objects WHERE log_id = ?", [log_id]).fetchone()[0]
    conn.execute("DROP TABLE staged")
    return ReadStats(events=kept, rejected_events=total - kept, objects=objects)


def _ingest(conn: duckdb.DuckDBPyConnection, log_id: str, upload: Path, folder: Path, source_format: str,
            mapping: dict[str, Any] | None) -> ReadStats:
    if source_format == "csv":
        return _load_csv_fast(conn, log_id, upload, mapping or {})
    stats = ReadStats()
    with upload.open("rb") as raw:
        reader = {"xes": read_xes, "ocel": read_ocel_json, "ocelxml": read_ocel_xml}[source_format](raw, stats=stats)
        paths = _stage(reader, folder)
    _load(conn, log_id, paths)
    return stats


def _receive(source: IO[bytes], folder: Path) -> Path:
    os.chmod(folder, 0o700)
    upload = folder / "upload"
    size = 0
    with upload.open("wb") as handle:
        while chunk := source.read(1 << 20):
            size += len(chunk)
            if size > MAX_IMPORT_BYTES:
                raise EventLogWorkspaceError("event log is larger than the supported limit")
            handle.write(chunk)
    return upload


def import_log(source: IO[bytes], *, name: str, source_format: str, mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Import an explicitly chosen local file. The source stream is read once."""
    if source_format not in {"csv", "xes", "ocel", "ocelxml"}:
        raise EventLogWorkspaceError("format must be csv, xes, ocel or ocelxml")
    if not isinstance(name, str) or not _NAME.fullmatch(name.strip()):
        raise EventLogWorkspaceError("a short log name is required")
    log_id = "log_" + secrets.token_hex(8)
    with _LOCK, tempfile.TemporaryDirectory(prefix="owg-eventlog-", dir=str(DATA_DIR)) as tmp:
        folder = Path(tmp)
        upload = _receive(source, folder)
        conn = _connect()
        try:
            if conn.execute("SELECT count(*) FROM logs").fetchone()[0] >= MAX_LOGS:
                raise EventLogWorkspaceError("delete an event log before importing another")
            conn.execute("BEGIN")
            try:
                stats = _ingest(conn, log_id, upload, folder, source_format, mapping)
                if stats.events == 0:
                    raise EventLogWorkspaceError("no events with a case, activity and timezone-qualified time")
                conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [log_id, name.strip(), source_format, datetime.now(timezone.utc).replace(tzinfo=None),
                              stats.events, stats.objects, stats.rejected_events])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return _check_log(conn, log_id)
        except EventLogFormatError as exc:
            raise EventLogWorkspaceError(str(exc)) from None
        finally:
            conn.close()


_FINGERPRINT = """SELECT e.event_id, e.activity, e.ts, e.resource,
      list(eo.object_id ORDER BY eo.object_id) FILTER (WHERE eo.object_id IS NOT NULL) AS objs
    FROM events e LEFT JOIN event_objects eo ON eo.log_id = e.log_id AND eo.event_id = e.event_id
    WHERE e.log_id = ? {window} GROUP BY e.event_id, e.activity, e.ts, e.resource"""


def append_log(log_id: str, source: IO[bytes], *, source_format: str,
               mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add a newer export to a saved log, skipping events it already holds.

    An event already in the log (same activity, time, resource and linked
    objects) is counted as a duplicate, so overlapping exports can be appended
    repeatedly. Appended events get a batch prefix on their identifier. Objects
    already in the log keep their attributes. Everything happens in one
    transaction: a conflict leaves the log unchanged.
    """
    if source_format not in {"csv", "xes", "ocel", "ocelxml"}:
        raise EventLogWorkspaceError("format must be csv, xes, ocel or ocelxml")
    staging = "stg_" + secrets.token_hex(8)
    with _LOCK, tempfile.TemporaryDirectory(prefix="owg-eventlog-", dir=str(DATA_DIR)) as tmp:
        folder = Path(tmp)
        conn = _connect()
        try:
            _check_log(conn, log_id)
            upload = _receive(source, folder)
            conn.execute("BEGIN")
            try:
                stats = _ingest(conn, staging, upload, folder, source_format, mapping)
                if stats.events == 0:
                    raise EventLogWorkspaceError("no events with a case, activity and timezone-qualified time")
                batch = conn.execute("SELECT coalesce(max(batch), 0) + 1 FROM log_appends WHERE log_id = ?",
                                     [log_id]).fetchone()[0]
                prefix = f"a{batch}:"
                low, high = conn.execute("SELECT min(ts), max(ts) FROM events WHERE log_id = ?", [staging]).fetchone()
                conn.execute(f"CREATE OR REPLACE TEMP TABLE new_fp AS {_FINGERPRINT.format(window='')}", [staging])
                conn.execute(f"CREATE OR REPLACE TEMP TABLE old_fp AS {_FINGERPRINT.format(window='AND e.ts BETWEEN ? AND ?')}",
                             [log_id, low, high])
                conn.execute("""CREATE OR REPLACE TEMP TABLE keep AS SELECT n.event_id FROM new_fp n
                    WHERE NOT EXISTS (SELECT 1 FROM old_fp o WHERE o.activity = n.activity AND o.ts = n.ts
                      AND o.resource = n.resource AND o.objs IS NOT DISTINCT FROM n.objs)""")
                added = conn.execute("SELECT count(*) FROM keep").fetchone()[0]
                conn.execute("""INSERT INTO events SELECT ?, ? || e.event_id, e.activity, e.ts, e.resource, e.attributes
                    FROM events e JOIN keep k ON k.event_id = e.event_id WHERE e.log_id = ?""", [log_id, prefix, staging])
                conn.execute("""INSERT INTO event_objects SELECT ?, ? || l.event_id, l.object_id, l.qualifier
                    FROM event_objects l JOIN keep k ON k.event_id = l.event_id WHERE l.log_id = ?""",
                             [log_id, prefix, staging])
                new_objects = conn.execute("""INSERT INTO objects SELECT ?, s.object_id, s.object_type, s.attributes
                    FROM objects s WHERE s.log_id = ? AND NOT EXISTS (SELECT 1 FROM objects t WHERE t.log_id = ?
                      AND t.object_id = s.object_id AND t.object_type = s.object_type)""",
                                           [log_id, staging, log_id]).fetchone()[0]
                for table, column in (("events", "event_id"), ("objects", "object_id")):
                    if conn.execute(f"SELECT 1 FROM {table} WHERE log_id = ? GROUP BY {column} HAVING count(*) > 1 LIMIT 1",
                                    [log_id]).fetchone():
                        raise EventLogWorkspaceError(
                            "appended identifiers conflict with the saved log (for example an object with another type)")
                for table in ("event_objects", "events", "objects"):
                    conn.execute(f"DELETE FROM {table} WHERE log_id = ?", [staging])
                duplicates = stats.events - added
                conn.execute("INSERT INTO log_appends VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             [log_id, batch, datetime.now(timezone.utc).replace(tzinfo=None), source_format,
                              added, duplicates, stats.rejected_events, new_objects])
                conn.execute("""UPDATE logs SET events = (SELECT count(*) FROM events WHERE log_id = ?),
                    objects = (SELECT count(*) FROM objects WHERE log_id = ?), rejected_events = rejected_events + ?
                    WHERE log_id = ?""", [log_id, log_id, stats.rejected_events, log_id])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            return {"log": _check_log(conn, log_id), "batch": batch, "added_events": added,
                    "duplicate_events_skipped": duplicates, "rejected_events": stats.rejected_events,
                    "added_objects": new_objects, "existing_object_attributes_kept": True,
                    "appended_event_id_prefix": prefix}
        except EventLogFormatError as exc:
            raise EventLogWorkspaceError(str(exc)) from None
        finally:
            conn.close()


def append_history(log_id: str) -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            _check_log(conn, log_id)
            rows = conn.execute("""SELECT batch, appended_at, source_format, added_events, duplicate_events,
                rejected_events, added_objects FROM log_appends WHERE log_id = ? ORDER BY batch""", [log_id]).fetchall()
        finally:
            conn.close()
    return {"appends": [{"batch": b, "appended_at": t.isoformat() + "Z", "source_format": f, "added_events": a,
                         "duplicate_events_skipped": d, "rejected_events": r, "added_objects": o}
                        for b, t, f, a, d, r, o in rows]}


def list_logs() -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            ids = [r[0] for r in conn.execute("SELECT log_id FROM logs ORDER BY imported_at DESC").fetchall()]
            return {"logs": [_check_log(conn, i) for i in ids], "limit": MAX_LOGS,
                    "stored_on_this_computer_only": True}
        finally:
            conn.close()


def delete_log(log_id: str) -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            _check_log(conn, log_id)
            for table in ("log_appends", "saved_analyses", "signal_rules", "event_objects", "events", "objects", "logs"):
                conn.execute(f"DELETE FROM {table} WHERE log_id = ?", [log_id])
            conn.execute("CHECKPOINT")
            return {"deleted": True, "log_id": log_id}
        finally:
            conn.close()


# ---------------------------------------------------------------- queries

def _case_type(conn: duckdb.DuckDBPyConnection, log_id: str, case_type: str | None) -> str:
    types = [r[0] for r in conn.execute("SELECT object_type FROM objects WHERE log_id = ? GROUP BY 1 ORDER BY count(*) DESC, 1",
                                        [log_id]).fetchall()]
    if not types:
        raise EventLogWorkspaceError("log has no case objects to select")
    if case_type:
        if case_type not in types:
            raise EventLogWorkspaceError("unknown object type for this log")
        return case_type
    return CASE_TYPE if CASE_TYPE in types else types[0]


_CASE_EVENTS = """
  SELECT eo.object_id AS case_id, e.activity, e.ts, e.resource, e.event_id, e.attributes
  FROM events e
  JOIN (SELECT DISTINCT log_id,event_id,object_id FROM event_objects) eo
    ON eo.log_id = e.log_id AND eo.event_id = e.event_id
  JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
  WHERE e.log_id = ? AND o.object_type = ?
"""


def _query(log_id: str, case_type: str | None, fn) -> dict[str, Any]:
    with _LOCK:
        conn = _connect()
        try:
            info = _check_log(conn, log_id)
            notion = _case_type(conn, log_id, case_type)
            # Materialize the case-notion projection once per request (temp, per connection).
            conn.execute(f"CREATE OR REPLACE TEMP TABLE ce AS {_CASE_EVENTS}", [log_id, notion])
            result = fn(conn)
            return {"log": info, "case_notion": notion, **result, **DESCRIPTIVE_ONLY}
        finally:
            conn.close()


def summary(log_id: str, case_type: str | None = None) -> dict[str, Any]:
    def run(conn):
        events, cases, activities, first, last = conn.execute(
            "SELECT count(*), count(DISTINCT case_id), count(DISTINCT activity), min(ts), max(ts) FROM ce").fetchone()
        durations = conn.execute("""SELECT quantile_cont(d, 0.5), quantile_cont(d, 0.9), avg(d) FROM (
            SELECT date_diff('second', min(ts), max(ts)) AS d FROM ce GROUP BY case_id)""").fetchone()
        return {"events": events, "cases": cases, "activities": activities,
                "first_event_at": first.isoformat() + "Z" if first else None,
                "last_event_at": last.isoformat() + "Z" if last else None,
                "case_duration_seconds": {"median": durations[0], "p90": durations[1], "mean": durations[2]}}
    return _query(log_id, case_type, run)


def object_types(log_id: str) -> dict[str, Any]:
    """Object-centric overview: types, counts, lifecycle activities and co-occurring types."""
    with _LOCK:
        conn = _connect()
        try:
            info = _check_log(conn, log_id)
            types = conn.execute("""SELECT o.object_type, count(DISTINCT o.object_id), count(eo.event_id)
                FROM objects o LEFT JOIN event_objects eo ON eo.log_id = o.log_id AND eo.object_id = o.object_id
                WHERE o.log_id = ? GROUP BY 1 ORDER BY 2 DESC, 1""", [log_id]).fetchall()
            relations = conn.execute("""WITH event_types AS (
                SELECT DISTINCT eo.event_id, o.object_type FROM event_objects eo JOIN objects o
                  ON o.log_id=eo.log_id AND o.object_id=eo.object_id WHERE eo.log_id=?)
                SELECT a.object_type, b.object_type, count(*)
                FROM event_types a JOIN event_types b
                  ON a.event_id = b.event_id AND a.object_type < b.object_type
                GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 50""", [log_id]).fetchall()
            return {"log": info, "object_types": [{"object_type": t, "objects": n, "event_links": e} for t, n, e in types],
                    "shared_events_between_types": [{"types": [a, b], "events": n} for a, b, n in relations],
                    **DESCRIPTIVE_ONLY}
        finally:
            conn.close()


def variants(log_id: str, case_type: str | None = None, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit), 200))
    def run(conn):
        rows = conn.execute(f"""WITH v AS (
              SELECT case_id, string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path,
                     date_diff('second', min(ts), max(ts)) AS duration
              FROM ce GROUP BY case_id)
            SELECT path, count(*) AS cases, quantile_cont(duration, 0.5) AS median_duration
            FROM v GROUP BY path ORDER BY cases DESC, path LIMIT {limit}""").fetchall()
        total, distinct = conn.execute(f"""SELECT count(*), count(DISTINCT path) FROM (
            SELECT string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id)""").fetchone()
        return {"cases": total, "distinct_variants": distinct, "variants": [
            {"path": path.split(_SEP), "cases": n, "share": round(n / total, 6) if total else 0,
             "median_duration_seconds": d} for path, n, d in rows]}
    return _query(log_id, case_type, run)


def directly_follows(log_id: str, case_type: str | None = None, limit: int = 200) -> dict[str, Any]:
    limit = max(1, min(int(limit), 2000))
    def run(conn):
        edges = conn.execute(f"""SELECT activity, nxt, count(*) AS n, count(DISTINCT case_id) AS cases,
              quantile_cont(gap, 0.5) AS median_gap FROM (
                SELECT case_id, activity,
                  lead(activity) OVER w AS nxt,
                  date_diff('second', ts, lead(ts) OVER w) AS gap
                FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            WHERE nxt IS NOT NULL GROUP BY 1, 2 ORDER BY n DESC, 1, 2 LIMIT {limit}""").fetchall()
        bounds = conn.execute("""SELECT kind, activity, count(*) FROM (
              SELECT 'start' AS kind, arg_min(activity, (ts, event_id)) AS activity FROM ce GROUP BY case_id
              UNION ALL SELECT 'end', arg_max(activity, (ts, event_id)) FROM ce GROUP BY case_id)
            GROUP BY 1, 2 ORDER BY 3 DESC""").fetchall()
        return {"edges": [{"from": a, "to": b, "transitions": n, "cases": c, "median_gap_seconds": g}
                          for a, b, n, c, g in edges],
                "start_activities": {a: n for k, a, n in bounds if k == "start"},
                "end_activities": {a: n for k, a, n in bounds if k == "end"},
                "gaps_are_observed_not_verified_waiting": True}
    return _query(log_id, case_type, run)


def activities(log_id: str, case_type: str | None = None) -> dict[str, Any]:
    def run(conn):
        rows = conn.execute("""SELECT activity, count(*) AS events, count(DISTINCT case_id) AS cases,
              count(DISTINCT CASE WHEN per_case > 1 THEN case_id END) AS repeated_in_cases,
              count(DISTINCT nullif(resource, '')) AS resources
            FROM (SELECT *, count(*) OVER (PARTITION BY case_id, activity) AS per_case FROM ce)
            GROUP BY 1 ORDER BY events DESC, 1 LIMIT 500""").fetchall()
        return {"activities": [{"activity": a, "events": e, "cases": c, "repeated_in_cases": r, "resources": res,
                                "repetition_is_not_verified_rework": True} for a, e, c, r, res in rows]}
    return _query(log_id, case_type, run)


def case_events(log_id: str, case_type: str | None = None) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """All events grouped by case, ordered; used by exports and analytics."""
    with _LOCK:
        conn = _connect()
        try:
            _check_log(conn, log_id)
            notion = _case_type(conn, log_id, case_type)
            cursor = conn.execute(_CASE_EVENTS + " ORDER BY case_id, ts, event_id", [log_id, notion])
            current, bucket = None, []
            while batch := cursor.fetchmany(10_000):
                for case_id, activity, ts, resource, event_id, attributes in batch:
                    if case_id != current and current is not None:
                        yield current, bucket
                        bucket = []
                    current = case_id
                    bucket.append({"case_id": case_id, "activity": activity,
                                   "timestamp": ts.replace(tzinfo=timezone.utc), "resource": resource,
                                   "event_id": event_id, "attributes": json.loads(attributes or "{}")})
            if current is not None:
                yield current, bucket
        finally:
            conn.close()


def export_log(log_id: str, export_format: str, out: IO[str], case_type: str | None = None) -> None:
    if export_format == "xes":
        write_xes(case_events(log_id, case_type), out)
    elif export_format == "csv":
        write_csv((event for _, events in case_events(log_id, case_type) for event in events), out)
    elif export_format in ("ocel", "ocelxml"):
        with _LOCK:
            conn = _connect()
            try:
                _check_log(conn, log_id)
                objects = [{"object_id": i, "object_type": t, "attributes": json.loads(a or "{}")}
                           for i, t, a in conn.execute("SELECT object_id, object_type, attributes FROM objects "
                                                       "WHERE log_id = ? ORDER BY 1", [log_id]).fetchall()]
                links: dict[str, list[tuple[str, str]]] = {}
                for event_id, object_id, qualifier in conn.execute(
                        "SELECT event_id, object_id, qualifier FROM event_objects WHERE log_id = ? ORDER BY 1, 2",
                        [log_id]).fetchall():
                    links.setdefault(event_id, []).append((object_id, qualifier))
                events = [{"event_id": i, "activity": a, "timestamp": ts.replace(tzinfo=timezone.utc),
                           "attributes": json.loads(attr or "{}"), "objects": links.get(i, [])}
                          for i, a, ts, attr in conn.execute(
                              "SELECT event_id, activity, ts, attributes FROM events WHERE log_id = ? "
                              "ORDER BY ts, event_id", [log_id]).fetchall()]
            finally:
                conn.close()
        (write_ocel_json if export_format == "ocel" else write_ocel_xml)(objects, events, out)
    else:
        raise EventLogWorkspaceError("export format must be csv, xes, ocel or ocelxml")


__all__ = [
    "EventLogWorkspaceError", "activities", "case_events", "delete_log", "directly_follows", "export_log",
    "import_log", "list_logs", "object_types", "summary", "variants", "database_path",
]
