"""Validate a bounded CSV sample with the normal import transform in isolation."""
from __future__ import annotations

import codecs
import csv
import io
import tempfile
from pathlib import Path
from typing import Any

from shared.event_log_formats import EventLogFormatError

from . import event_log_workspace as workspace

MAX_BYTES = 512 * 1024
MAX_ROWS = 2000


def _prefix(data: bytes, delimiter: str, partial: bool) -> tuple[str, bool]:
    """Keep complete logical CSV records, including quoted multiline fields."""
    try:
        decoder = codecs.getincrementaldecoder("utf-8-sig")()
        text = decoder.decode(data, final=not partial)
    except UnicodeError:
        raise workspace.EventLogWorkspaceError("CSV must use UTF-8 text") from None
    stream = io.StringIO(text, newline="")
    end = 0
    records = 0
    try:
        for _ in csv.reader(stream, delimiter=delimiter, strict=True):
            position = stream.tell()
            if partial and position == len(text) and not text.endswith(("\n", "\r")):
                break
            end = position
            records += 1
            if records == MAX_ROWS + 1:  # include the header
                break
    except csv.Error as error:
        if not (partial and stream.tell() == len(text) and str(error) == "unexpected end of data"):
            raise workspace.EventLogWorkspaceError("CSV records could not be read within the preview limits") from None
    if records < 2:
        raise workspace.EventLogWorkspaceError("No complete data rows fit in the CSV preview")
    return text[:end], partial or end < len(text)


def preview_csv(data: bytes, mapping: dict[str, Any], *, partial: bool = False) -> dict[str, Any]:
    delimiter = mapping.get("delimiter") or ","
    if delimiter not in {",", ";", "\t", "|"}:
        raise workspace.EventLogWorkspaceError("unsupported delimiter")
    if len(data) > MAX_BYTES:
        raise workspace.EventLogWorkspaceError("CSV preview exceeds its supported size")
    sample, limited = _prefix(data, delimiter, partial)
    try:
        import duckdb
    except ImportError:
        raise workspace.EventLogWorkspaceError(
            "event-log workspace requires the duckdb package; reinstall OpenWorkGraph") from None

    reasons: dict[str, int] = {}
    with tempfile.TemporaryDirectory(prefix="owg-csv-preview-") as folder:
        path = Path(folder) / "sample.csv"
        path.write_bytes(sample.encode("utf-8"))
        with duckdb.connect(":memory:", config={"threads": 2}) as conn:
            conn.execute(workspace._SCHEMA)
            try:
                stats = workspace._load_csv_fast(conn, "preview", path, mapping, reasons)
            except EventLogFormatError as error:
                raise workspace.EventLogWorkspaceError(str(error)) from None
            except duckdb.Error:
                raise workspace.EventLogWorkspaceError("CSV sample could not be parsed with this mapping") from None
            rows = conn.execute("""SELECT links.object_id, e.activity, e.ts, e.resource
                FROM events e JOIN event_objects links USING (log_id, event_id)
                ORDER BY cast(substr(e.event_id, 2) AS BIGINT) LIMIT 5""").fetchall()
    identity = getattr(stats, "source_identity", None) or {}
    duplicates = identity.get("duplicate_source_ids_skipped", 0)
    assumed = reasons.pop("timestamps_with_assumed_offset", 0)
    return {
        "sample_rows": stats.events + stats.rejected_events + duplicates,
        "sample_limited": limited,
        "accepted_events": stats.events,
        "rejected_rows": stats.rejected_events,
        "distinct_cases": stats.objects,
        "duplicate_source_ids_skipped": duplicates,
        "timestamps_with_assumed_offset": assumed,
        "rejected_by_reason": {reason: count for reason, count in reasons.items() if count},
        "examples": [
            {"case_id": case_id, "activity": activity, "timestamp": timestamp.isoformat() + "Z", "resource": resource}
            for case_id, activity, timestamp, resource in rows
        ],
    }
