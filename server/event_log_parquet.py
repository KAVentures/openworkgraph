"""Read a Parquet file as CSV, using the DuckDB engine the workspace already relies on.

Data warehouses and lakes (Snowflake, BigQuery, Databricks, Spark) export tables as Parquet. The
workspace imports CSV, so the dashboard converts the file to CSV here and then uses the ordinary CSV
import (column mapping, consent, quality reports) unchanged, as it does for Excel worksheets. Nothing
is stored: the upload is written to a temporary file in the data folder and removed afterwards.

The file must start and end with the Parquet magic bytes and is read with ``read_parquet`` in an
in-memory DuckDB connection that cannot install or load extensions. Values become text: NULL as an
empty cell, timestamps with a time zone as ISO 8601 in UTC with ``Z``, timestamps without one as ISO
8601 without an offset (the import then asks which offset they use), dates as ISO dates, binary
values left empty and counted, lists and structs as compact JSON. At most ``MAX_ROWS`` rows and
``MAX_COLUMNS`` columns are read.
"""
from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import IO, Any

import duckdb

FORMAT = "openworkgraph.event-log-parquet-conversion.v1"
MAX_ROWS = 2_000_000
MAX_COLUMNS = 500
MAX_BYTES = 512 * 1024 * 1024        # upload limit enforced by the route
_MAGIC = b"PAR1"


class ParquetError(ValueError):
    """The file is not a readable Parquet file, or exceeds a limit."""


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return _text(value, [0])
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return None
    return str(value)


def _text(value: Any, blobs: list[int]) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
        return value.isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        blobs[0] += 1
        return ""
    if isinstance(value, (list, dict, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_json_default)
    return str(value)


def to_csv(source: IO[bytes], *, folder: Path) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="owg-parquet-", dir=str(folder)) as tmp:
        path = Path(tmp) / "upload.parquet"
        with open(path, "wb") as out:
            shutil.copyfileobj(source, out)
        size = path.stat().st_size
        with open(path, "rb") as check:
            head = check.read(4)
            check.seek(max(0, size - 4))
            tail = check.read(4)
        if size < 12 or head != _MAGIC or tail != _MAGIC:
            raise ParquetError("the file is not a Parquet file")
        conn = duckdb.connect(":memory:", config={"autoinstall_known_extensions": False,
                                                   "autoload_known_extensions": False})
        try:
            try:
                described = conn.execute("DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()
            except duckdb.Error:
                raise ParquetError("the Parquet file could not be read") from None
            columns = [d[0] for d in described]
            kinds = [str(d[1]).upper() for d in described]
            naive = any(k in ("TIMESTAMP", "TIMESTAMP_NS", "TIMESTAMP_MS", "TIMESTAMP_S") for k in kinds)
            if not columns:
                raise ParquetError("the file has no columns")
            if len(columns) > MAX_COLUMNS:
                raise ParquetError(f"the file has more than {MAX_COLUMNS} columns")
            # Zoned values are converted to UTC in SQL (fetching them as Python objects needs pytz).
            zoned = {i for i, k in enumerate(kinds) if k == "TIMESTAMP WITH TIME ZONE"}
            quoted = ['"' + c.replace('"', '""') + '"' for c in columns]
            select = ", ".join(f"timezone('UTC', {q})" if i in zoned else f"CAST({q} AS VARCHAR)" if kinds[i] == "TIME WITH TIME ZONE"
                               else q for i, q in enumerate(quoted))
            out = io.StringIO()
            writer = csv.writer(out, lineterminator="\n")
            writer.writerow(columns)
            rows, blobs = 0, [0]
            try:
                cursor = conn.execute(f"SELECT {select} FROM read_parquet(?)", [str(path)])
                while batch := cursor.fetchmany(10_000):
                    rows += len(batch)
                    if rows > MAX_ROWS:
                        raise ParquetError(f"the file has more than {MAX_ROWS:,} rows")
                    writer.writerows([(_text(v, blobs) + "Z" if i in zoned and v is not None else _text(v, blobs))
                                      for i, v in enumerate(record)] for record in batch)
            except duckdb.Error:
                raise ParquetError("the Parquet file could not be read") from None
        finally:
            conn.close()
    if not rows:
        raise ParquetError("the file has no rows")
    text = out.getvalue()
    return {"format": FORMAT, "sheets": ["rows"], "sheet": "rows", "kind": "parquet", "rows": rows,
            "columns": len(columns), "csv": text, "binary_cells_left_empty": blobs[0],
            "dates_have_no_offset": naive, "stored": False}


__all__ = ["to_csv", "ParquetError", "FORMAT"]
