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
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import IO, Any

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
            "dates_have_no_offset": True, "stored": False}


__all__ = ["to_csv", "SqliteError", "FORMAT"]
