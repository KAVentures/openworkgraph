"""Save the cases matching a filter as a new event log.

Analysts often want to study a slice of a log on its own: one region, one
quarter, the cases that went through an escalation. Deriving copies every event
of the matching cases (under the chosen case notion), the objects those events
are linked to, their links and the objects' timed attribute values into a new
saved log. The source log is unchanged, and the new log can be analysed,
exported, compared with the source or deleted like any other.

Not copied: the source's activity mapping, saved filters and analyses, signals,
domain packs, source-identity keys, object-to-object relationships, quality
reports and case tags. The derived log starts without them, and the response says so.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-derived.v1"
NOT_COPIED = ("activity mapping", "saved filters and analyses", "signals", "domain packs", "source-identity keys",
              "object-to-object relationships", "quality reports", "case tags")


class DeriveSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    cohort: dict[str, Any]
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> tuple[DeriveSpec, dict[str, Any]]:
    try:
        parsed = DeriveSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "derived log settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    if not workspace._NAME.fullmatch(parsed.name.strip()):
        raise workspace.EventLogWorkspaceError("a short log name is required")
    chosen = cohorts.parse_cohort(parsed.cohort)
    if not {k for k in chosen if k != "label"}:
        raise workspace.EventLogWorkspaceError("choose a case filter; an unfiltered copy would duplicate the log")
    return parsed, chosen


def derive(log_id: str, spec: Any) -> dict[str, Any]:
    settings, chosen = _parse(spec)
    new_id = "log_" + secrets.token_hex(8)

    def run(conn):
        source_cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        cohorts.restrict(conn, chosen, log_id)
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        if not cases:
            raise workspace.EventLogWorkspaceError("no cases match the filter")
        if conn.execute("SELECT count(*) FROM logs").fetchone()[0] >= workspace.MAX_LOGS:
            raise workspace.EventLogWorkspaceError("delete an event log before saving another")
        source_format = conn.execute("SELECT source_format FROM logs WHERE log_id = ?", [log_id]).fetchone()[0]
        conn.execute("CREATE OR REPLACE TEMP TABLE dv_events AS SELECT DISTINCT event_id FROM ce")
        conn.execute("BEGIN")
        try:
            conn.execute("""INSERT INTO events SELECT ?, e.event_id, e.activity, e.ts, e.resource, e.attributes
                FROM events e WHERE e.log_id = ? AND e.event_id IN (SELECT event_id FROM dv_events)""", [new_id, log_id])
            conn.execute("""INSERT INTO event_objects SELECT ?, eo.event_id, eo.object_id, eo.qualifier
                FROM event_objects eo WHERE eo.log_id = ? AND eo.event_id IN (SELECT event_id FROM dv_events)""",
                         [new_id, log_id])
            conn.execute("""INSERT INTO objects SELECT ?, o.object_id, o.object_type, o.attributes
                FROM objects o WHERE o.log_id = ?
                  AND o.object_id IN (SELECT DISTINCT object_id FROM event_objects WHERE log_id = ?)""",
                         [new_id, log_id, new_id])
            conn.execute("""INSERT INTO object_attribute_values SELECT ?, v.object_id, v.name, v.valid_from, v.value
                FROM object_attribute_values v WHERE v.log_id = ?
                  AND v.object_id IN (SELECT object_id FROM objects WHERE log_id = ?)""", [new_id, log_id, new_id])
            events = conn.execute("SELECT count(*) FROM events WHERE log_id = ?", [new_id]).fetchone()[0]
            objects = conn.execute("SELECT count(*) FROM objects WHERE log_id = ?", [new_id]).fetchone()[0]
            conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [new_id, settings.name.strip(), source_format,
                          datetime.now(timezone.utc).replace(tzinfo=None), events, objects, 0])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"source_cases": source_cases, "cases": cases, "events": events, "objects": objects}

    data = workspace._query(log_id, settings.case_type, run)
    created = next(log for log in workspace.list_logs()["logs"] if log["log_id"] == new_id)
    return {
        "format": FORMAT,
        "log": created,
        "derived_from": {"log_id": log_id, "case_notion": data["case_notion"], "cohort": cohorts.describe(chosen),
                         "cases_matched": data["cases"], "cases_in_source": data["source_cases"]},
        "copied": {"events": data["events"], "objects": data["objects"]},
        "not_copied": list(NOT_COPIED),
        "source_unchanged": True,
        "interpretation": ("Every event of each matching case is copied, so a case keeps its full history. In an "
                           "object-centric log an event linked to a matching case also brings its other objects; "
                           "those objects keep only the copied events."),
    }
