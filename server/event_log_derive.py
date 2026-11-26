"""Save the cases matching a filter as a new event log.

Analysts often want to study a slice of a log on its own: one region, one
quarter, the cases that went through an escalation. Deriving copies every event
of the matching cases (under the chosen case notion), the objects those events
are linked to, their timed attribute values and supplied relationships between
those objects into a new saved log. The new log can be analysed, exported,
compared with the source or deleted like any other.

An optional time window (``events_from``, ``events_before``) trims the copy to the events inside it,
the "trim to timeframe" of other process-mining tools: a case keeps only its events in the window,
and a matching case with no event in it is left out. Without a window every event is copied.

An optional activity trim (``trim_from_activity``, ``trim_to_activity``, ``trim_mode``) keeps each
matching case's events from the first occurrence of one activity to the last (``longest``) or the next
(``first``) occurrence of another, the endpoints trim of other process-mining tools. Either endpoint
can be left out to trim only the start or only the end of each case. Activity names are those of the
analysis (after any activity mapping); events in between are kept whatever they are, hidden
activities included. A matching case without the endpoints is left out.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from shared.event_log_formats import parse_timestamp

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-derived.v1"
NOT_COPIED = ("activity mapping", "saved filters and analyses", "signals", "domain packs", "source-identity keys",
              "quality reports", "case tags", "cost model")


_RETAINED_RELATIONSHIPS = """
    FROM object_relationships r
    SEMI JOIN dv_objects source ON r.source_object_id = source.object_id
    SEMI JOIN dv_objects target ON r.target_object_id = target.object_id
    WHERE r.log_id = ?
"""


class SelectionSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    events_from: str | None = Field(default=None, min_length=10, max_length=40)
    events_before: str | None = Field(default=None, min_length=10, max_length=40)
    trim_from_activity: str | None = Field(default=None, min_length=1, max_length=500)
    trim_to_activity: str | None = Field(default=None, min_length=1, max_length=500)
    trim_mode: Literal["longest", "first"] = "longest"


def activity_trim(settings: Any) -> dict[str, Any] | None:
    """The activity trim of parsed settings, or None when neither endpoint is chosen."""
    if settings.trim_from_activity is None and settings.trim_to_activity is None:
        return None
    return {"from_activity": settings.trim_from_activity, "to_activity": settings.trim_to_activity,
            "mode": settings.trim_mode}


class DeriveSpec(SelectionSpec):
    name: str = Field(min_length=1, max_length=120)


def _window(events_from: str | None, events_before: str | None) -> tuple[datetime | None, datetime | None]:
    bounds = []
    for key, value in (("events_from", events_from), ("events_before", events_before)):
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
    if (not {k for k in chosen if k != "label"} and _window(parsed.events_from, parsed.events_before) == (None, None)
            and activity_trim(parsed) is None):
        raise workspace.EventLogWorkspaceError(
            "choose a case filter or a time window, or an activity trim; an unfiltered copy would duplicate the log")
    return parsed, chosen


def _select_original_events(conn, log_id: str, window_from: datetime | None,
                            window_before: datetime | None, trim: dict[str, Any] | None = None) -> tuple[int, int, int]:
    # The mapped projection determines which cases match. A collapsed
    # analysis row still belongs to the original trace being copied.
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_events AS
        SELECT DISTINCT links.event_id
        FROM event_objects links SEMI JOIN ce ON links.object_id = ce.case_id
        WHERE links.log_id = ?""", [log_id])
    before_trim = None
    if window_from or window_before or trim:
        before_trim = conn.execute("SELECT count(*) FROM dv_events").fetchone()[0]
    if window_from or window_before:
        conn.execute("""DELETE FROM dv_events WHERE event_id IN (SELECT event_id FROM events WHERE log_id = ?
            AND ((?::TIMESTAMP IS NOT NULL AND ts < ?::TIMESTAMP) OR (?::TIMESTAMP IS NOT NULL AND ts >= ?::TIMESTAMP)))""",
                     [log_id, window_from, window_from, window_before, window_before])
    events = conn.execute("SELECT count(*) FROM dv_events").fetchone()[0]
    if not events:
        raise workspace.EventLogWorkspaceError("no event of the matching cases falls in the time window")
    windowed = events
    if trim:
        _trim_to_activities(conn, log_id, trim)
        events = conn.execute("SELECT count(*) FROM dv_events").fetchone()[0]
        if not events:
            raise workspace.EventLogWorkspaceError("no matching case has events between the trim activities")
    return events, before_trim - windowed if before_trim is not None else 0, windowed - events


def _trim_to_activities(conn, log_id: str, trim: dict[str, Any]) -> None:
    """Keep, per matching case, only the events between its trim endpoints (inclusive).

    Endpoints are found in the analysis projection ``ce`` ordered by time and event ID; the
    original events of a case at or between the two endpoints are kept. An event linked to several
    matching cases is kept when it lies inside the endpoints of at least one of them; the kept
    (case, event) pairs are left in ``dv_case_events`` for flat exports.
    """
    start, end, mode = trim["from_activity"], trim["to_activity"], trim["mode"]
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_ordered AS
        SELECT case_id, activity, ts, event_id,
               row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS n
        FROM ce SEMI JOIN (SELECT DISTINCT event_id FROM dv_events) kept USING (event_id)""")
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_start AS
        SELECT case_id, min(n) AS sn FROM dv_ordered
        WHERE ?::VARCHAR IS NULL OR activity = ? GROUP BY case_id""", [start, start])
    if end is None:
        end_query = "SELECT o.case_id, max(o.n) AS en FROM dv_ordered o JOIN dv_start s USING (case_id) WHERE o.n >= s.sn GROUP BY o.case_id"
        params: list[Any] = []
    else:
        aggregate = "max" if mode == "longest" else "min"
        # In "first" mode the next occurrence of the same activity ends the segment.
        step = 1 if mode == "first" and start == end else 0
        end_query = f"""SELECT o.case_id, {aggregate}(o.n) AS en FROM dv_ordered o JOIN dv_start s USING (case_id)
            WHERE o.activity = ? AND o.n >= s.sn + ? GROUP BY o.case_id"""
        params = [end, step]
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE dv_bounds AS
        SELECT s.case_id, a.ts AS s_ts, a.event_id AS s_id, b.ts AS e_ts, b.event_id AS e_id
        FROM dv_start s JOIN ({end_query}) e USING (case_id)
        JOIN dv_ordered a ON a.case_id = s.case_id AND a.n = s.sn
        JOIN dv_ordered b ON b.case_id = e.case_id AND b.n = e.en""", params)
    # Kept (case, event) pairs, so a flat export of an object-centric log keeps each case's own segment.
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_case_events AS
        SELECT DISTINCT links.object_id AS case_id, d.event_id FROM dv_events d
        JOIN events ev ON ev.log_id = ? AND ev.event_id = d.event_id
        JOIN event_objects links ON links.log_id = ? AND links.event_id = d.event_id
        JOIN dv_bounds b ON b.case_id = links.object_id
        WHERE (ev.ts > b.s_ts OR (ev.ts = b.s_ts AND ev.event_id >= b.s_id))
          AND (ev.ts < b.e_ts OR (ev.ts = b.e_ts AND ev.event_id <= b.e_id))""", [log_id, log_id])
    conn.execute("CREATE OR REPLACE TEMP TABLE dv_events AS SELECT DISTINCT event_id FROM dv_case_events")
    for table in ("dv_ordered", "dv_start", "dv_bounds"):
        conn.execute(f"DROP TABLE {table}")


def _select_events(conn, log_id: str, chosen: dict[str, Any],
                   window_from: datetime | None, window_before: datetime | None,
                   trim: dict[str, Any] | None = None) -> dict[str, int]:
    source_cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
    cohorts.restrict(conn, chosen, log_id)
    cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
    if not cases:
        raise workspace.EventLogWorkspaceError("no cases match the filter")
    events, trimmed, trimmed_by_activity = _select_original_events(conn, log_id, window_from, window_before, trim)
    kept_cases = cases
    if window_from or window_before or trim:
        # A surviving raw event can have been collapsed in the analytical projection.
        kept_cases = conn.execute("""SELECT count(DISTINCT links.object_id) FROM event_objects links
            SEMI JOIN ce ON links.object_id = ce.case_id
            SEMI JOIN dv_events ON links.event_id = dv_events.event_id WHERE links.log_id = ?""", [log_id]).fetchone()[0]
    conn.execute("""CREATE OR REPLACE TEMP TABLE dv_objects AS
        SELECT DISTINCT links.object_id FROM event_objects links
        SEMI JOIN dv_events ON links.event_id = dv_events.event_id WHERE links.log_id = ?""", [log_id])
    return {"source_cases": source_cases, "cases": cases, "events": events,
            "trimmed": trimmed, "trimmed_by_activity": trimmed_by_activity, "kept_cases": kept_cases}


def preview(log_id: str, spec: Any) -> dict[str, Any]:
    settings, chosen = _parse(spec, SelectionSpec)
    start, end = _window(settings.events_from, settings.events_before)

    def run(conn):
        selected = _select_events(conn, log_id, chosen, start, end, activity_trim(settings))
        objects = conn.execute("SELECT count(*) FROM dv_objects").fetchone()[0]
        relationships = conn.execute("SELECT count(*) " + _RETAINED_RELATIONSHIPS, [log_id]).fetchone()[0]
        return {**selected, "objects": objects, "object_relationships": relationships}

    return workspace._query(log_id, settings.case_type, run)


def derive(log_id: str, spec: Any) -> dict[str, Any]:
    settings, chosen = _parse(spec, DeriveSpec)
    window_from, window_before = _window(settings.events_from, settings.events_before)
    trim = activity_trim(settings)
    new_id = "log_" + secrets.token_hex(8)

    def run(conn):
        if conn.execute("SELECT count(*) FROM logs").fetchone()[0] >= workspace.MAX_LOGS:
            raise workspace.EventLogWorkspaceError("delete an event log before saving another")
        selected = _select_events(conn, log_id, chosen, window_from, window_before, trim)
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
                  AND o.object_id IN (SELECT object_id FROM dv_objects)""", [new_id, log_id])
            conn.execute("""INSERT INTO object_attribute_values SELECT ?, v.object_id, v.name, v.valid_from, v.value
                FROM object_attribute_values v WHERE v.log_id = ?
                  AND v.object_id IN (SELECT object_id FROM objects WHERE log_id = ?)""", [new_id, log_id, new_id])
            relationships = conn.execute("""INSERT INTO object_relationships
                SELECT ?, r.source_object_id, r.target_object_id, r.qualifier """ +
                _RETAINED_RELATIONSHIPS, [new_id, log_id]).fetchone()[0]
            objects = conn.execute("SELECT count(*) FROM objects WHERE log_id = ?", [new_id]).fetchone()[0]
            conn.execute("INSERT INTO logs VALUES (?, ?, ?, ?, ?, ?, ?)",
                         [new_id, settings.name.strip(), source_format,
                          datetime.now(timezone.utc).replace(tzinfo=None), selected["events"], objects, 0])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {**selected, "objects": objects, "object_relationships": relationships}

    data = workspace._query(log_id, settings.case_type, run)
    created = next(log for log in workspace.list_logs()["logs"] if log["log_id"] == new_id)
    return {
        "format": FORMAT,
        "log": created,
        "derived_from": {"log_id": log_id, "case_notion": data["case_notion"], "cohort": cohorts.describe(chosen),
                         "cases_matched": data["cases"], "cases_in_source": data["source_cases"]},
        "copied": {"events": data["events"], "objects": data["objects"],
                   **({"object_relationships": data["object_relationships"]} if data["object_relationships"] else {})},
        **({"time_window": {"events_from": window_from.isoformat() + "Z" if window_from else None,
                            "events_before": window_before.isoformat() + "Z" if window_before else None,
                            "events_outside_left_out": data["trimmed"],
                            "cases_with_events_in_window": data["kept_cases"]}}
           if window_from or window_before else {}),
        **({"activity_trim": {**trim, "events_left_out": data["trimmed_by_activity"], "cases_with_endpoints": data["kept_cases"]}}
           if trim else {}),
        "not_copied": list(NOT_COPIED),
        "source_unchanged": True,
        "interpretation": (("Only the events of the matching cases inside the time window are copied. " if window_from or window_before
                            else "" if trim else "Every event of each matching case is copied, so a case keeps its full history. ") +
                           ("Each matching case keeps only its events between the trim activities. " if trim else "") + "In an "
                           "object-centric log an event linked to a matching case also brings its other objects; "
                           "those objects keep only the copied events."),
    }
