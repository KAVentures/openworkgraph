"""Combine saved event logs into one new log.

The same process is often exported in pieces: one file per region or per year, or one system each for
the order and the invoice steps. Combining copies the events, objects, event-object links and timed
object attributes of 2 to 10 saved logs into a new saved log. The sources are unchanged.

Case identity is the owner's choice. With ``cases="shared"`` an ID that appears in several sources is
one case (or object) whose events come from all of them, which joins the systems of one process. With
``cases="separate"`` every source keeps its own cases, and IDs are prefixed with the source number so
equal IDs from different exports never merge. Event IDs are always prefixed (``s1:``, ``s2:``...), so
events never collide, and every event gets a ``source_log`` attribute naming the log it came from.

Not copied, as with a derived log: activity mappings, saved filters and analyses, signals, domain
packs, source-identity keys, object-to-object relationships, quality reports, case tags, cost models.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event-log-combined.v1"
MAX_SOURCES = 10
SOURCE_ATTRIBUTE = "source_log"
NOT_COPIED = ("activity mapping", "saved filters and analyses", "signals", "domain packs", "source-identity keys",
              "object-to-object relationships", "quality reports", "case tags", "cost model")


class CombineSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    log_ids: list[str] = Field(min_length=2, max_length=MAX_SOURCES)
    cases: Literal["shared", "separate"] = "shared"


def _parse(spec: Any) -> CombineSpec:
    try:
        parsed = CombineSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise EventLogWorkspaceError("combine settings are invalid: " + (where + " " if where else "") + first["msg"]) from None
    if not workspace._NAME.fullmatch(parsed.name.strip()):
        raise EventLogWorkspaceError("a short log name is required")
    if len(set(parsed.log_ids)) != len(parsed.log_ids):
        raise EventLogWorkspaceError("choose each log only once")
    return parsed


def combine(spec: Any) -> dict[str, Any]:
    settings = _parse(spec)
    new_id = "log_" + secrets.token_hex(8)
    oid = "o.object_id" if settings.cases == "shared" else "'s' || s.k || ':' || o.object_id"
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            sources = [workspace._check_log(conn, log_id) for log_id in settings.log_ids]
            if conn.execute("SELECT count(*) FROM logs").fetchone()[0] >= workspace.MAX_LOGS:
                raise EventLogWorkspaceError("delete an event log before saving another")
            conn.execute("CREATE OR REPLACE TEMP TABLE cb_sources (log_id VARCHAR, k INTEGER, name VARCHAR)")
            conn.executemany("INSERT INTO cb_sources VALUES (?, ?, ?)",
                             [[s["log_id"], k, s["name"]] for k, s in enumerate(sources, start=1)])
            overwritten = conn.execute(f"""SELECT count(*) FROM events e JOIN cb_sources s USING (log_id)
                WHERE json_exists(e.attributes, '$.{SOURCE_ATTRIBUTE}')""").fetchone()[0]
            shared, conflicts = conn.execute("""SELECT count(*) FILTER (WHERE n > 1), count(*) FILTER (WHERE types > 1)
                FROM (SELECT o.object_id, count(DISTINCT o.log_id) AS n, count(DISTINCT o.object_type) AS types
                      FROM objects o JOIN cb_sources s USING (log_id) GROUP BY o.object_id)""").fetchone()
            formats = {s["source_format"] for s in sources}
            conn.execute("BEGIN")
            try:
                conn.execute(f"""INSERT INTO events SELECT ?, 's' || s.k || ':' || e.event_id, e.activity, e.ts, e.resource,
                    CAST(json_merge_patch(e.attributes, json_object('{SOURCE_ATTRIBUTE}', s.name)) AS VARCHAR)
                    FROM events e JOIN cb_sources s USING (log_id)""", [new_id])
                conn.execute(f"""INSERT INTO event_objects SELECT ?, 's' || s.k || ':' || o.event_id, {oid}, o.qualifier
                    FROM event_objects o JOIN cb_sources s USING (log_id)""", [new_id])
                # A shared ID keeps the type and attributes of the first source that has it.
                conn.execute(f"""INSERT INTO objects SELECT ?, id, arg_min(object_type, k), arg_min(attributes, k)
                    FROM (SELECT {oid} AS id, o.object_type, o.attributes, s.k FROM objects o JOIN cb_sources s USING (log_id))
                    GROUP BY id""", [new_id])
                conn.execute(f"""INSERT INTO object_attribute_values SELECT DISTINCT ?, {oid}, o.name, o.valid_from, o.value
                    FROM object_attribute_values o JOIN cb_sources s USING (log_id)""", [new_id])
                events = conn.execute("SELECT count(*) FROM events WHERE log_id = ?", [new_id]).fetchone()[0]
                objects = conn.execute("SELECT count(*) FROM objects WHERE log_id = ?", [new_id]).fetchone()[0]
                conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [new_id, settings.name.strip(), formats.pop() if len(formats) == 1 else "ocel",
                              datetime.now(timezone.utc).replace(tzinfo=None), events, objects, 0])
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
    created = next(log for log in workspace.list_logs()["logs"] if log["log_id"] == new_id)
    return {
        "format": FORMAT, "log": created, "cases": settings.cases,
        "combined_from": [{"log_id": s["log_id"], "name": s["name"], "events": s["events"], "objects": s["objects"],
                           "event_id_prefix": f"s{k}:"} for k, s in enumerate(sources, start=1)],
        "copied": {"events": events, "objects": objects},
        "ids_in_several_sources": shared, "object_type_conflicts": conflicts,
        "source_attribute": SOURCE_ATTRIBUTE, "source_attribute_replaced_on_events": overwritten,
        "not_copied": list(NOT_COPIED), "sources_unchanged": True,
        "interpretation": ("Shared: an ID found in several logs is one case or object with the events of all of them; "
                           "its type and attributes come from the first log listed that has it."
                           if settings.cases == "shared" else
                           "Separate: every log keeps its own cases; object IDs are prefixed with the source number."),
    }


__all__ = ["combine", "FORMAT", "MAX_SOURCES"]
