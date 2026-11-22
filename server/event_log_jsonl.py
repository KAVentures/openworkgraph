"""Read a JSON Lines file (one JSON object per line) as CSV, with the standard library only.

APIs, log shippers and many SaaS exports write events as JSON Lines (.jsonl, .ndjson). The workspace
imports CSV, so the dashboard converts the file to CSV here and then uses the ordinary CSV import
(column mapping, consent, quality reports) unchanged, as it does for Excel worksheets. Nothing is
stored.

Each non-empty line must be a JSON object. Nested objects become dotted columns (``customer.id``),
lists and other non-scalar values are kept as compact JSON text, ``null`` becomes an empty cell and
booleans become ``true``/``false``. Columns appear in the order they are first seen. At most
``MAX_ROWS`` lines, ``MAX_COLUMNS`` columns, ``MAX_DEPTH`` levels of nesting and ``MAX_LINE_BYTES``
per line are read. The input is scanned twice to keep memory usage bounded, and the
converted CSV response has a size limit.
"""
from __future__ import annotations

import csv
import io
import json
from typing import IO, Any

FORMAT = "openworkgraph.event-log-jsonl-conversion.v1"
MAX_ROWS = 2_000_000
MAX_COLUMNS = 500
MAX_DEPTH = 5
MAX_LINE_BYTES = 1 << 20
MAX_BYTES = 256 * 1024 * 1024        # upload limit enforced by the route
MAX_CSV_CHARS = 32 * 1024 * 1024     # bound response JSON size in memory


class JsonlError(ValueError):
    """The file is not JSON Lines, or exceeds a limit."""


def _flatten(value: Any, prefix: str, out: dict[str, Any], depth: int) -> None:
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, dict) and item and depth < MAX_DEPTH:
            _flatten(item, name, out, depth + 1)
        else:
            out[name] = item


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, str)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _flat_records(source: IO[bytes]):
    """Parse and validate one flat record at a time; never retain the input's rows."""
    for number, raw in enumerate(iter(lambda: source.readline(MAX_LINE_BYTES + 1), b""), start=1):
        if len(raw) > MAX_LINE_BYTES:
            raise JsonlError(f"line {number} is longer than {MAX_LINE_BYTES:,} bytes")
        line = raw.strip()
        if not line:
            continue
        if number == 1 and line.startswith(b"\xef\xbb\xbf"):
            line = line[3:]
        try:
            value = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            raise JsonlError(f"line {number} is not valid JSON") from None
        if not isinstance(value, dict):
            raise JsonlError(f"line {number} is not a JSON object")
        flat: dict[str, Any] = {}
        _flatten(value, "", flat, 1)
        yield flat


def to_csv(source: IO[bytes]) -> dict[str, Any]:
    """Convert a seekable, bounded upload in two passes instead of keeping every JSON row in RAM."""
    try:
        beginning = source.tell()
    except (OSError, AttributeError):
        raise JsonlError("the JSON Lines input must be seekable") from None
    columns: dict[str, int] = {}
    records = 0
    for flat in _flat_records(source):
        records += 1
        if records > MAX_ROWS:
            raise JsonlError(f"the file has more than {MAX_ROWS:,} lines")
        for name in flat:
            if name not in columns:
                if len(columns) >= MAX_COLUMNS:
                    raise JsonlError(f"the file has more than {MAX_COLUMNS} columns")
                columns[name] = len(columns)
    if not records:
        raise JsonlError("the file has no JSON objects")
    try:
        source.seek(beginning)
    except (OSError, AttributeError):
        raise JsonlError("the JSON Lines input must be seekable") from None
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    names = list(columns)
    writer.writerow(names)
    if out.tell() > MAX_CSV_CHARS:
        raise JsonlError("the converted CSV exceeds the supported response size")
    for flat in _flat_records(source):
        writer.writerow([_text(flat.get(name)) for name in names])
        if out.tell() > MAX_CSV_CHARS:
            raise JsonlError("the converted CSV exceeds the supported response size")
    return {"format": FORMAT, "sheets": ["lines"], "sheet": "lines", "kind": "lines", "rows": records,
            "columns": len(names), "csv": out.getvalue(), "dates_have_no_offset": False, "stored": False}


__all__ = ["to_csv", "JsonlError", "FORMAT"]
