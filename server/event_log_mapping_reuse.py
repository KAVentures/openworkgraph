"""Reuse the column mapping recorded with an earlier CSV import.

Every import stores its declared transform (columns, delimiter, offset
decision, timestamp format) in the log's quality report. When a new file's
header has the columns an earlier mapping needs, the dashboard can offer that
mapping instead of asking the user to map every column again, for example for
this month's export of the same system.

Only the column header is compared. A match says the same column names exist,
not that the columns mean the same thing, so the mapping is offered, never
applied silently. Source event identity settings are not reused.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-mapping-matches.v1"
MAX_MATCHES = 5


class Header(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str] = Field(min_length=1, max_length=1000)
    delimiter: str = Field(default=",", pattern="^(,|;|\t|\\||t)$")


class MatchSpec(Header):
    other_headers: list[Header] = Field(default_factory=list, max_length=3)
    preferred_log_id: str | None = Field(default=None, max_length=128)


def _mapping(declared: dict[str, Any]) -> dict[str, Any] | None:
    mapping = declared.get("mapping") if isinstance(declared, dict) else None
    if declared.get("format") != "csv" or not isinstance(mapping, dict):
        return None
    keys = ("case_column", "activity_column", "timestamp_column", "resource_column", "attribute_columns",
            "delimiter", "naive_timestamp_offset", "timestamp_format", "time_column")
    out = {k: mapping[k] for k in keys if mapping.get(k) not in (None, "", [])}
    return out if all(out.get(k) for k in ("case_column", "activity_column", "timestamp_column")) else None


def matches(spec: Any) -> dict[str, Any]:
    """Earlier CSV mappings whose case, activity, timestamp and resource columns are all in ``columns``."""
    try:
        settings = MatchSpec.model_validate(spec if spec is not None else {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "mapping match settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    headers: dict[str, set[str]] = {}
    for header in (settings, *settings.other_headers):
        delimiter = "\t" if header.delimiter == "t" else header.delimiter
        if delimiter in headers:
            raise workspace.EventLogWorkspaceError("provide one header per delimiter")
        headers[delimiter] = set(header.columns)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            rows = conn.execute("""SELECT l.log_id, l.name, q.created_at, q.batch, q.report FROM logs l
                JOIN log_quality q USING (log_id)
                QUALIFY row_number() OVER (PARTITION BY l.log_id ORDER BY q.created_at DESC, q.batch DESC) = 1
                ORDER BY CASE WHEN l.log_id = ? THEN 0 ELSE 1 END, q.created_at DESC, q.batch DESC, l.log_id""",
                                [settings.preferred_log_id]).fetchall()
        finally:
            conn.close()
    found: list[dict[str, Any]] = []
    for log_id, name, created_at, batch, report in rows:
        try:
            mapping = _mapping(json.loads(report).get("transform") or {})
        except (ValueError, AttributeError):
            mapping = None
        if mapping is None:
            continue
        columns = headers.get(mapping.get("delimiter") or ",")
        if columns is None:
            continue
        required = [mapping[k] for k in ("case_column", "activity_column", "timestamp_column", "resource_column")
                    if mapping.get(k)]
        if not all(c in columns for c in required):
            continue
        attributes = mapping.get("attribute_columns") or []
        mapping["attribute_columns"] = [c for c in attributes if c in columns]
        found.append({"log_id": log_id, "name": name, "mapped_at": created_at.isoformat() + "Z", "batch": batch,
                      "mapping": mapping, "missing_attribute_columns": [c for c in attributes if c not in columns]})
        if len(found) >= MAX_MATCHES:
            break
    return {"format": FORMAT, "matches": found,
            "note": "Matched by column names only; check that the columns still mean the same before importing."}
