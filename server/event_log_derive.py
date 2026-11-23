"""Save the cases matching a filter as a new event log.

Analysts often want to study a slice of a log on its own: one region, one
quarter, the cases that went through an escalation. Deriving copies every event
of the matching cases (under the chosen case notion), the objects those events
are linked to, their links and the objects' timed attribute values into a new
saved log. The source log is unchanged, and the new log can be analysed,
exported, compared with the source or deleted like any other.

Not copied: the source's activity mapping, saved filters and analyses, signals,
domain packs, source-identity keys, object-to-object relationships, quality
reports, case tags and the cost model. The derived log starts without them, and the response says so.

An optional time window (``events_from``, ``events_before``) trims the copy to the events inside it,
the "trim to timeframe" of other process-mining tools: a case keeps only its events in the window,
and a matching case with no event in it is left out. Without a window every event is copied.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.event_log_formats import parse_timestamp

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-derived.v1"
NOT_COPIED = ("activity mapping", "saved filters and analyses", "signals", "domain packs", "source-identity keys",
              "object-to-object relationships", "quality reports", "case tags", "cost model")


class SelectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    events_from: str | None = Field(default=None, min_length=10, max_length=40)
    events_before: str | None = Field(default=None, min_length=10, max_length=40)


class DeriveSpec(SelectionSpec):
    name: str = Field(min_length=1, max_length=120)


def _window(parsed: SelectionSpec) -> tuple[datetime | None, datetime | None]:
    bounds = []
    for key in ("events_from", "events_before"):
        value = getattr(parsed, key)
        stamp = parse_timestamp(value) if value is not None else None
        if value is not None and stamp is None:
            raise workspace.EventLogWorkspaceError(f"{key} must be a time with a UTC offset")
        bounds.append(stamp.astimezone(timezone.utc).replace(tzinfo=None) if stamp else None)
    if bounds[0] and bounds[1] and bounds[0] >= bounds[1]:
        raise workspace.EventLogWorkspaceError("events_from must be before events_before")
    return bounds[0], bounds[1]


_Selection = TypeVar("_Selection", bound=SelectionSpec)


def _parse(spec: Any, schema: type[_Selection]) -> tuple[_Selection, dict[str, Any]]:
    try:
        parsed = schema.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "derived log settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    if isinstance(parsed, DeriveSpec) and not workspace._NAME.fullmatch(parsed.name.strip()):
        raise workspace.EventLogWorkspaceError("a short log name is required")
    chosen = cohorts.parse_cohort(parsed.cohort)
    if not {k for k in chosen if k != "label"} and _window(parsed) == (None, None):
        raise workspace.EventLogWorkspaceError("choose a case filter or a time window; an unfiltered copy would duplicate the log")
    return parsed, chosen


def _select_events(conn, log_id: str, chosen: dict[str, Any],
                   window_from: datetime | None, window_before: datetime | None) -> dict[str, int]:
    source_cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
    cohorts.restrict(conn, chosen, log_id)
    cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
    if not cases:
        raise workspace.EventLogWorkspaceError("no cases match the filter")
    # The mapped projection determines which cases match. A collapsed
    # analysis row still belongs to the original trace being copied.
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_events AS
        SELECT DISTINCT links.event_id
        FROM event_objects links SEMI JOIN ce ON links.object_id = ce.case_id
        WHERE links.log_id = ?""", [log_id])
    before_trim = None
    if window_from or window_before:
        before_trim = conn.execute("SELECT count(*) FROM dv_events").fetchone()[0]
        conn.execute("""DELETE FROM dv_events WHERE event_id IN (SELECT event_id FROM events WHERE log_id = ?
            AND ((?::TIMESTAMP IS NOT NULL AND ts < ?::TIMESTAMP) OR (?::TIMESTAMP IS NOT NULL AND ts >= ?::TIMESTAMP)))""",
                     [log_id, window_from, window_from, window_before, window_before])
    events = conn.execute("SELECT count(*) FROM dv_events").fetchone()[0]
    if not events:
        raise workspace.EventLogWorkspaceError("no event of the matching cases falls in the time window")
    kept_cases = cases
    if before_trim is not None:
        # A surviving raw event can have been collapsed in the analytical projection.
        kept_cases = conn.execute("""SELECT count(DISTINCT links.object_id) FROM event_objects links
            SEMI JOIN ce ON links.object_id = ce.case_id
            SEMI JOIN dv_events ON links.event_id = dv_events.event_id WHERE links.log_id = ?""", [log_id]).fetchone()[0]
    return {"source_cases": source_cases, "cases": cases, "events": events,
            "trimmed": before_trim - events if before_trim is not None else 0, "kept_cases": kept_cases}


def preview(log_id: str, spec: Any) -> dict[str, Any]:
    settings, chosen = _parse(spec, SelectionSpec)
    start, end = _window(settings)

    def run(conn):
        selected = _select_events(conn, log_id, chosen, start, end)
        objects = conn.execute("""SELECT count(DISTINCT links.object_id) FROM event_objects links
            SEMI JOIN dv_events ON links.event_id = dv_events.event_id WHERE links.log_id = ?""", [log_id]).fetchone()[0]
        return {**selected, "objects": objects}

    return workspace._query(log_id, settings.case_type, run)


def derive(log_id: str, spec: Any) -> dict[str, Any]:
    settings, chosen = _parse(spec, DeriveSpec)
    window_from, window_before = _window(settings)
    new_id = "log_" + secrets.token_hex(8)

    def run(conn):
        if conn.execute("SELECT count(*) FROM logs").fetchone()[0] >= workspace.MAX_LOGS:
            raise workspace.EventLogWorkspaceError("delete an event log before saving another")
        selected = _select_events(conn, log_id, chosen, window_from, window_before)
        source_format = conn.execute("SELECT source_format FROM logs WHERE log_id = ?", [log_id]).fetchone()[0]
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
            objects = conn.execute("SELECT count(*) FROM objects WHERE log_id = ?", [new_id]).fetchone()[0]
            conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [new_id, settings.name.strip(), source_format,
                          datetime.now(timezone.utc).replace(tzinfo=None), selected["events"], objects, 0])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {**selected, "objects": objects}

    data = workspace._query(log_id, settings.case_type, run)
    created = next(log for log in workspace.list_logs()["logs"] if log["log_id"] == new_id)
    return {
        "format": FORMAT,
        "log": created,
        "derived_from": {"log_id": log_id, "case_notion": data["case_notion"], "cohort": cohorts.describe(chosen),
                         "cases_matched": data["cases"], "cases_in_source": data["source_cases"]},
        "copied": {"events": data["events"], "objects": data["objects"]},
        **({"time_window": {"events_from": window_from.isoformat() + "Z" if window_from else None,
                            "events_before": window_before.isoformat() + "Z" if window_before else None,
                            "events_outside_left_out": data["trimmed"],
                            "cases_with_events_in_window": data["kept_cases"]}}
           if window_from or window_before else {}),
        "not_copied": list(NOT_COPIED),
        "source_unchanged": True,
        "interpretation": (("Only the events of the matching cases inside the time window are copied. " if window_from or window_before
                            else "Every event of each matching case is copied, so a case keeps its full history. ") + "In an "
                           "object-centric log an event linked to a matching case also brings its other objects; "
                           "those objects keep only the copied events."),
    }
