"""Read one table of a SQLite database file as CSV, with the standard library only.

Many line-of-business tools and exports keep their data in a SQLite file (.sqlite, .db). The workspace
imports CSV, so the dashboard converts a chosen table to CSV here and then uses the ordinary CSV import
(column mapping, consent, quality reports) unchanged, exactly as it does for Excel worksheets. Nothing
is stored: the upload is written to a temporary file in the data folder and removed afterwards.

Safety and limits:

* the file must start with the SQLite header; it is opened read-only and immutable, with
  ``trusted_schema`` off and ``query_only`` on, and extensions stay disabled;
* only real tables are offered (``sqlite_master`` type ``table``, not views, virtual tables or
  SQLite's own tables), and each is read with a plain ``SELECT *``;
* a progress handler stops a read that runs longer than ``MAX_SECONDS``;
* at most ``MAX_ROWS`` rows and ``MAX_COLUMNS`` columns are read; the generated CSV is also
  capped before it is returned as JSON to avoid unbounded process memory usage.

Values become text: NULL as an empty cell, integers and reals as written, text as stored. Binary
(BLOB) values are left empty and counted. SQLite stores no time zone, so the CSV import asks which
offset times without one use.
"""
from __future__ import annotations

import csv
import io
import json
import re
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from contextlib import closing, contextmanager
from typing import IO, Any, Iterator

FORMAT = "openworkgraph.event-log-sqlite-conversion.v1"
MAX_ROWS = 2_000_000
MAX_COLUMNS = 500
MAX_TABLES = 500
MAX_SECONDS = 120
MAX_CSV_CHARS = 32 * 1024 * 1024
_HEADER = b"SQLite format 3\x00"


class SqliteError(ValueError):
    """The file is not a readable SQLite database, or exceeds a limit."""


def _tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("""SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        AND sql NOT LIKE 'CREATE VIRTUAL TABLE%' ORDER BY name""").fetchall()
    return [r[0] for r in rows][:MAX_TABLES]


def _cell(value: Any, blobs: list[int]) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        blobs[0] += 1
        return ""
    return str(value)


def to_csv(source: IO[bytes], table: str | None = None, *, folder: Path) -> dict[str, Any]:
    """Convert ``table`` (default: the first table by name) of the uploaded database to CSV."""
    with tempfile.TemporaryDirectory(prefix="owg-sqlite-", dir=str(folder)) as tmp:
        path = Path(tmp) / "upload.sqlite"
        with open(path, "wb") as out:
            shutil.copyfileobj(source, out)
        with open(path, "rb") as check:
            if check.read(len(_HEADER)) != _HEADER:
                raise SqliteError("the file is not a SQLite database")
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            raise SqliteError("the SQLite database could not be opened") from None
        cursor = None
        try:
            deadline = time.monotonic() + MAX_SECONDS
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            try:
                conn.execute("PRAGMA trusted_schema = OFF")
                conn.execute("PRAGMA query_only = ON")
                tables = _tables(conn)
                ocel = is_ocel(conn)
            except sqlite3.Error:
                raise SqliteError("the SQLite database could not be read") from None
            if not tables:
                raise SqliteError("the database has no tables")
            chosen = tables[0] if table is None else table
            if chosen not in tables:
                raise SqliteError("no table with this name in the database")
            quoted = '"' + chosen.replace('"', '""') + '"'
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            rows, blobs = 0, [0]
            try:
                cursor = conn.execute(f"SELECT * FROM {quoted}")
                columns = [d[0] for d in cursor.description or []]
                if not columns:
                    raise SqliteError("the table has no columns")
                if len(columns) > MAX_COLUMNS:
                    raise SqliteError(f"the table has more than {MAX_COLUMNS} columns")
                writer.writerow(columns)
                if out.tell() > MAX_CSV_CHARS:
                    raise SqliteError("the converted CSV exceeds the supported response size")
                for record in cursor:
                    rows += 1
                    if rows > MAX_ROWS:
                        raise SqliteError(f"the table has more than {MAX_ROWS:,} rows")
                    writer.writerow([_cell(v, blobs) for v in record])
                    if out.tell() > MAX_CSV_CHARS:
                        raise SqliteError("the converted CSV exceeds the supported response size")
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc):
                    raise SqliteError(f"reading the table took longer than {MAX_SECONDS} seconds") from None
                raise SqliteError("the table could not be read") from None
            except sqlite3.Error:
                raise SqliteError("the table could not be read") from None
        finally:
            # Windows will not unlink the temporary SQLite file while a cursor
            # still holds a native statement handle, including on error paths.
            # Finalize the statement before closing the connection and temp dir.
            try:
                if cursor is not None:
                    cursor.close()
            finally:
                conn.close()
    if not rows:
        raise SqliteError("the table has no rows")
    return {"format": FORMAT, "sheets": tables, "sheet": chosen, "kind": "table", "rows": rows,
            "columns": len(columns), "csv": out.getvalue(), "binary_cells_left_empty": blobs[0],
            "dates_have_no_offset": True, "stored": False,
            **({"ocel": True} if ocel else {})}



@contextmanager
def _opened(source: IO[bytes], folder: Path) -> Iterator[sqlite3.Connection]:
    """Open a temporary, immutable, read-only SQLite file with untrusted schema disabled."""
    with tempfile.TemporaryDirectory(prefix="owg-ocel-sqlite-", dir=str(folder)) as tmp:
        path = Path(tmp) / "upload.sqlite"
        with open(path, "wb") as out:
            shutil.copyfileobj(source, out)
        with open(path, "rb") as check:
            if check.read(len(_HEADER)) != _HEADER:
                raise SqliteError("the file is not a SQLite database")
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        except sqlite3.Error:
            raise SqliteError("the SQLite database could not be opened") from None
        try:
            deadline = time.monotonic() + MAX_SECONDS
            conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
            try:
                conn.execute("PRAGMA trusted_schema = OFF")
                conn.execute("PRAGMA query_only = ON")
            except sqlite3.Error:
                raise SqliteError("the SQLite database could not be read") from None
            yield conn
        finally:
            conn.close()


OCEL_FORMAT = "openworkgraph.event-log-ocel-sqlite-conversion.v1"
_OCEL_TABLES = {"event", "object", "event_object", "object_object", "event_map_type", "object_map_type"}
MAX_OCEL_ROWS = 5_000_000
_OFFSET = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def is_ocel(conn: sqlite3.Connection) -> bool:
    """The database uses the OCEL 2.0 SQLite schema (its standard tables are all present)."""
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    return _OCEL_TABLES <= names


def _time(value: Any, naive: list[int]) -> str:
    text = str(value).strip().replace(" ", "T", 1)
    if _OFFSET.search(text):
        return text
    naive[0] += 1
    return text + "Z"


def _rows(conn: sqlite3.Connection, sql: str, budget: list[int]):
    with closing(conn.execute(sql)) as cursor:
        for row in cursor:
            budget[0] += 1
            if budget[0] > MAX_OCEL_ROWS:
                raise SqliteError(f"the OCEL database has more than {MAX_OCEL_ROWS:,} rows")
            yield row


def to_ocel_json(source: IO[bytes], *, folder: Path) -> dict[str, Any]:
    """Read an OCEL 2.0 SQLite database as an OCEL 2.0 JSON document for the ordinary OCEL import.

    Times without an offset (as some tools write them) are read as UTC and counted."""
    naive, budget = [0], [0]
    with _opened(source, folder) as conn:
        try:
            if not is_ocel(conn):
                raise SqliteError("the database is not in the OCEL 2.0 SQLite format")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            q = lambda n: '"' + n.replace('"', '""') + '"'
            events: dict[str, dict[str, Any]] = {}
            for ocel_id, ocel_type in _rows(conn, "SELECT ocel_id, ocel_type FROM event", budget):
                events[str(ocel_id)] = {"id": str(ocel_id), "type": str(ocel_type), "time": None, "attributes": [],
                                        "relationships": []}
            for ocel_type, suffix in conn.execute("SELECT ocel_type, ocel_type_map FROM event_map_type").fetchall():
                table = f"event_{suffix}"
                if table not in tables:
                    continue
                # Finalize SQLite statements before temporary-file cleanup, even on row-limit errors.
                with closing(conn.execute(f"SELECT * FROM {q(table)}")) as cursor:
                    names = [d[0] for d in cursor.description]
                    for row in cursor:
                        budget[0] += 1
                        if budget[0] > MAX_OCEL_ROWS:
                            raise SqliteError(f"the OCEL database has more than {MAX_OCEL_ROWS:,} rows")
                        record = dict(zip(names, row))
                        event = events.get(str(record.get("ocel_id")))
                        if event is None:
                            continue
                        if record.get("ocel_time") is not None:
                            event["time"] = _time(record["ocel_time"], naive)
                        event["attributes"] = [{"name": n, "value": str(v)} for n, v in record.items()
                                               if n not in ("ocel_id", "ocel_time") and v is not None]
            objects: dict[str, dict[str, Any]] = {}
            for ocel_id, ocel_type in _rows(conn, "SELECT ocel_id, ocel_type FROM object", budget):
                objects[str(ocel_id)] = {"id": str(ocel_id), "type": str(ocel_type), "attributes": [], "relationships": []}
            for ocel_type, suffix in conn.execute("SELECT ocel_type, ocel_type_map FROM object_map_type").fetchall():
                table = f"object_{suffix}"
                if table not in tables:
                    continue
                # Finalize SQLite statements before temporary-file cleanup, even on row-limit errors.
                with closing(conn.execute(f"SELECT * FROM {q(table)}")) as cursor:
                    names = [d[0] for d in cursor.description]
                    for row in cursor:
                        budget[0] += 1
                        if budget[0] > MAX_OCEL_ROWS:
                            raise SqliteError(f"the OCEL database has more than {MAX_OCEL_ROWS:,} rows")
                        record = dict(zip(names, row))
                        obj = objects.get(str(record.get("ocel_id")))
                        if obj is None:
                            continue
                        at = _time(record.get("ocel_time") or "1970-01-01T00:00:00Z", naive)
                        changed = record.get("ocel_changed_field")
                        for n, v in record.items():
                            if n in ("ocel_id", "ocel_time", "ocel_changed_field") or v is None or (changed and n != changed):
                                continue
                            obj["attributes"].append({"name": n, "time": at, "value": str(v)})
            for event_id, object_id, qualifier in _rows(conn, "SELECT ocel_event_id, ocel_object_id, ocel_qualifier FROM event_object", budget):
                if str(event_id) in events:
                    events[str(event_id)]["relationships"].append({"objectId": str(object_id), "qualifier": str(qualifier or "")})
            for source_id, target_id, qualifier in _rows(conn, "SELECT ocel_source_id, ocel_target_id, ocel_qualifier FROM object_object", budget):
                if str(source_id) in objects:
                    objects[str(source_id)]["relationships"].append({"objectId": str(target_id), "qualifier": str(qualifier or "")})
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc):
                raise SqliteError(f"reading the database took longer than {MAX_SECONDS} seconds") from None
            raise SqliteError("the OCEL database could not be read") from None
        except sqlite3.Error:
            raise SqliteError("the OCEL database could not be read") from None
    timed = [e for e in events.values() if e["time"]]
    if not timed:
        raise SqliteError("the OCEL database has no events with a time")
    document = {"objectTypes": [{"name": t, "attributes": []} for t in sorted({o["type"] for o in objects.values()})],
                "eventTypes": [{"name": t, "attributes": []} for t in sorted({e["type"] for e in timed})],
                "objects": list(objects.values()), "events": timed}
    return {"format": OCEL_FORMAT, "events": len(timed), "objects": len(objects),
            "events_without_time": len(events) - len(timed), "times_without_offset_read_as_utc": naive[0],
            "document": json.dumps(document, ensure_ascii=False), "stored": False}


__all__ = ["to_csv", "to_ocel_json", "is_ocel", "SqliteError", "FORMAT", "OCEL_FORMAT"]
