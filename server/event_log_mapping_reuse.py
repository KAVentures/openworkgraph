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


class MatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    columns: list[str] = Field(min_length=1, max_length=1000)
    delimiter: str = Field(default=",", pattern="^(,|;|\t|\\||t)$")


def _mapping(declared: dict[str, Any]) -> dict[str, Any] | None:
    mapping = declared.get("mapping") if isinstance(declared, dict) else None
    if declared.get("format") != "csv" or not isinstance(mapping, dict):
        return None
    keys = ("case_column", "activity_column", "timestamp_column", "resource_column", "attribute_columns",
            "delimiter", "naive_timestamp_offset", "timestamp_format")
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
    columns = set(settings.columns)
    delimiter = "\t" if settings.delimiter == "t" else settings.delimiter
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            rows = conn.execute("""SELECT l.log_id, l.name, q.created_at, q.batch, q.report FROM logs l
                JOIN log_quality q USING (log_id) ORDER BY q.created_at DESC, q.batch DESC""").fetchall()
        finally:
            conn.close()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for log_id, name, created_at, batch, report in rows:
        if log_id in seen:
            continue
        seen.add(log_id)  # only the newest mapping of each log is considered
        try:
            mapping = _mapping(json.loads(report).get("transform") or {})
        except (ValueError, AttributeError):
            mapping = None
        if mapping is None or (mapping.get("delimiter") or ",") != delimiter:
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
