"""Activity mappings: group low-level activities into higher-level ones (event abstraction, R9).

A mapping lists groups, each a label and the original activities it stands for, and
can collapse consecutive repeats of the same (mapped) activity within a case into its
first event. Mappings are saved per log; at most one is applied at a time. While one
is applied, every case-centric analysis (variants, maps, KPIs, conformance,
predictions, simulation, ...) sees the mapped activities, and every result says so in
``activity_mapping``. The stored events are never changed: removing the mapping
restores the original view. Saved signal rules and object-centric views keep the
original activities.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import secrets
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace

MAX_MAPPINGS_PER_LOG = 50
MAX_GROUPS = 200
Label = Annotated[str, Field(min_length=1, max_length=200)]

_DDL = """CREATE TABLE IF NOT EXISTS activity_mappings (
  log_id VARCHAR NOT NULL, mapping_id VARCHAR NOT NULL, created_at TIMESTAMP NOT NULL,
  definition VARCHAR NOT NULL, applied BOOLEAN NOT NULL DEFAULT FALSE)"""


class Group(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Label
    activities: list[Label] = Field(min_length=1, max_length=500)


class Mapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Label
    groups: list[Group] = Field(min_length=1, max_length=MAX_GROUPS)
    collapse_repeats: bool = False


def _ensure(conn) -> None:
    conn.execute(_DDL)


def _parse(payload: Any) -> Mapping:
    try:
        mapping = Mapping.model_validate(payload)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "activity mapping is invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    seen: dict[str, str] = {}
    for group in mapping.groups:
        for activity in group.activities:
            if activity in seen and seen[activity] != group.label:
                raise workspace.EventLogWorkspaceError(f"activity {activity!r} is in two groups")
            seen[activity] = group.label
    return mapping


def _digest(definition: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _describe(mapping_id: str, created, definition: str, applied: bool) -> dict[str, Any]:
    body = json.loads(definition)
    return {"mapping_id": mapping_id, "created_at": created.isoformat() + "Z" if created else None, "applied": bool(applied),
            "definition_sha256": _digest(body), **body,
            "mapped_activities": sum(len(g["activities"]) for g in body["groups"])}


def _with_conn(log_id: str, fn):
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            _ensure(conn)
            return fn(conn)
        finally:
            conn.close()


def listing(log_id: str) -> dict[str, Any]:
    def run(conn):
        rows = conn.execute("SELECT mapping_id, created_at, definition, applied FROM activity_mappings WHERE log_id = ? "
                            "ORDER BY created_at, mapping_id", [log_id]).fetchall()
        activities = conn.execute("SELECT activity, count(*) FROM events WHERE log_id = ? GROUP BY activity "
                                  "ORDER BY count(*) DESC, activity LIMIT 2000", [log_id]).fetchall()
        return {"log_id": log_id, "mappings": [_describe(*r) for r in rows],
                "original_activities": [{"activity": a, "events": n} for a, n in activities],
                "max_mappings": MAX_MAPPINGS_PER_LOG}
    return _with_conn(log_id, run)


def create(log_id: str, payload: Any) -> dict[str, Any]:
    mapping = _parse(payload)

    def run(conn):
        if conn.execute("SELECT count(*) FROM activity_mappings WHERE log_id = ?", [log_id]).fetchone()[0] >= MAX_MAPPINGS_PER_LOG:
            raise workspace.EventLogWorkspaceError(f"a log keeps at most {MAX_MAPPINGS_PER_LOG} activity mappings")
        known = {r[0] for r in conn.execute("SELECT DISTINCT activity FROM events WHERE log_id = ?", [log_id]).fetchall()}
        unknown = sorted({a for g in mapping.groups for a in g.activities} - known)
        mapping_id = "amap_" + secrets.token_hex(8)
        conn.execute("INSERT INTO activity_mappings VALUES (?, ?, ?, ?, FALSE)",
                     [log_id, mapping_id, datetime.now(timezone.utc).replace(tzinfo=None),
                      json.dumps(mapping.model_dump())])
        row = conn.execute("SELECT mapping_id, created_at, definition, applied FROM activity_mappings "
                           "WHERE log_id = ? AND mapping_id = ?", [log_id, mapping_id]).fetchone()
        return {**_describe(*row), "activities_not_in_log": unknown[:50]}
    return _with_conn(log_id, run)


def _require(conn, log_id: str, mapping_id: str) -> None:
    if not conn.execute("SELECT 1 FROM activity_mappings WHERE log_id = ? AND mapping_id = ?",
                        [log_id, str(mapping_id)]).fetchone():
        raise workspace.EventLogWorkspaceError("unknown activity mapping")


def apply_mapping(log_id: str, mapping_id: str | None) -> dict[str, Any]:
    """Apply one mapping to the log's analyses (``None`` removes any)."""
    def run(conn):
        if mapping_id is not None:
            _require(conn, log_id, mapping_id)
        conn.execute("UPDATE activity_mappings SET applied = (mapping_id = ?) WHERE log_id = ?",
                     [mapping_id or "", log_id])
        return listing_rows(conn, log_id)
    return _with_conn(log_id, run)


def listing_rows(conn, log_id: str) -> dict[str, Any]:
    rows = conn.execute("SELECT mapping_id, created_at, definition, applied FROM activity_mappings WHERE log_id = ? "
                        "ORDER BY created_at, mapping_id", [log_id]).fetchall()
    return {"log_id": log_id, "mappings": [_describe(*r) for r in rows]}


def delete(log_id: str, mapping_id: str) -> dict[str, Any]:
    def run(conn):
        _require(conn, log_id, mapping_id)
        conn.execute("DELETE FROM activity_mappings WHERE log_id = ? AND mapping_id = ?", [log_id, mapping_id])
        return listing_rows(conn, log_id)
    return _with_conn(log_id, run)


def erase(conn, log_id: str) -> None:
    _ensure(conn)
    conn.execute("DELETE FROM activity_mappings WHERE log_id = ?", [log_id])


def apply_to_case_events(conn, log_id: str) -> dict[str, Any] | None:
    """Relabel the request's ``ce`` table with the applied mapping, if any; describe what was done."""
    _ensure(conn)
    row = conn.execute("SELECT mapping_id, created_at, definition, applied FROM activity_mappings "
                       "WHERE log_id = ? AND applied", [log_id]).fetchone()
    if row is None:
        return None
    described = _describe(*row)
    pairs = [(a, g["label"]) for g in described["groups"] for a in g["activities"]]
    before = conn.execute("SELECT count(*) FROM ce").fetchone()[0]
    conn.execute("CREATE OR REPLACE TEMP TABLE ce_map AS SELECT unnest(?) AS original, unnest(?) AS label",
                 [[p[0] for p in pairs], [p[1] for p in pairs]])
    relabeled = conn.execute("SELECT count(*) FROM ce JOIN ce_map m ON ce.activity = m.original").fetchone()[0]
    conn.execute("UPDATE ce SET activity = m.label FROM ce_map m WHERE ce.activity = m.original")
    collapsed = 0
    if described["collapse_repeats"]:
        conn.execute("""CREATE OR REPLACE TEMP TABLE ce_rep AS SELECT case_id, event_id FROM (
              SELECT case_id, event_id, activity,
                     lag(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS previous FROM ce)
            WHERE previous = activity""")
        collapsed = conn.execute("SELECT count(*) FROM ce_rep").fetchone()[0]
        conn.execute("DELETE FROM ce WHERE (case_id, event_id) IN (SELECT case_id, event_id FROM ce_rep)")
    return {"mapping_id": described["mapping_id"], "name": described["name"],
            "definition_sha256": described["definition_sha256"], "groups": len(described["groups"]),
            "events_relabeled": relabeled, "repeats_collapsed": collapsed, "events_before": before,
            "original_events_unchanged": True}


__all__ = ["apply_mapping", "apply_to_case_events", "create", "delete", "erase", "listing"]
