"""Object-centric conformance rules over saved event logs (#370 R24).

Case-level compliance rules check one object type's lifecycle at a time. These rules check how object
types relate, without flattening the log onto one case notion:

- ``event_cardinality``: every event of activity A is linked to between ``min`` and ``max`` objects of
  type T ("every Pay event involves exactly one invoice");
- ``object_cardinality``: every object of type T is related to between ``min`` and ``max`` objects of
  type U ("every order has one to ten items"). Two objects are related when they share an event, or
  only an event of ``via_activity`` when one is given;
- ``synchronization``: every event of activity A linked to an object of type T comes after an event of
  activity B for each object of type U related to that object ("an order ships only after every one of
  its items is picked").

Events are ordered by time, then event ID, the same tie-break as the rest of the workspace. A rule whose
trigger never occurs is satisfied vacuously and counted separately; a synchronization with no related
objects is too. Relating objects stops at a disclosed work budget instead of returning partial counts.
Results describe the recorded links of the imported log only: they are not verified business
relationships, audit evidence or legal compliance.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_workspace as workspace
from .event_log_analytics import _wilson

MAX_RULES = 20
MAX_EXAMPLES = 50
MAX_RELATION_PAIRS = 5_000_000      # object pairs formed through shared events, per rule
Label = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[StrictInt, Field(ge=0, le=10 ** 6)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventCardinality(_Strict):
    template: Literal["event_cardinality"]
    activity: Label
    object_type: Label
    min: Count = 0
    max: Count | None = None


class ObjectCardinality(_Strict):
    template: Literal["object_cardinality"]
    object_type: Label
    related_type: Label
    via_activity: Label | None = None
    min: Count = 0
    max: Count | None = None


class Synchronization(_Strict):
    template: Literal["synchronization"]
    activity: Label
    object_type: Label
    related_type: Label
    required_activity: Label


Rule = Annotated[Union[EventCardinality, ObjectCardinality, Synchronization], Field(discriminator="template")]


class RuleSet(_Strict):
    rules: list[Rule] = Field(min_length=1, max_length=MAX_RULES)


def parse(spec: Any) -> RuleSet:
    try:
        parsed = RuleSet.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "object rules are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    for rule in parsed.rules:
        if isinstance(rule, (EventCardinality, ObjectCardinality)):
            if rule.max is not None and rule.max < rule.min:
                raise workspace.EventLogWorkspaceError("a rule's max must not be below its min")
            if rule.min == 0 and rule.max is None:
                raise workspace.EventLogWorkspaceError("a cardinality rule needs a min above 0 or a max")
        if isinstance(rule, Synchronization) and rule.activity == rule.required_activity \
                and rule.object_type == rule.related_type:
            raise workspace.EventLogWorkspaceError("a synchronization needs a different activity or object type")
    return parsed


def _bounds(rule) -> str:
    if rule.max is None:
        return f"at least {rule.min}"
    return f"exactly {rule.min}" if rule.min == rule.max else f"between {rule.min} and {rule.max}"


def describe(rule) -> str:
    if isinstance(rule, EventCardinality):
        return f"every {rule.activity} event is linked to {_bounds(rule)} {rule.object_type} objects"
    if isinstance(rule, ObjectCardinality):
        via = f" through {rule.via_activity} events" if rule.via_activity else ""
        return f"every {rule.object_type} is related to {_bounds(rule)} {rule.related_type} objects{via}"
    return (f"every {rule.activity} event of {rule.object_type} objects comes after {rule.required_activity} "
            f"of each related {rule.related_type}")


# Each template materializes ``oc`` as (unit, activated, violated, detail); ``unit`` is an event ID, an
# object ID, or "event / object" for synchronization occurrences.
def _event_cardinality(conn, log_id: str, rule: EventCardinality) -> None:
    conn.execute("""CREATE OR REPLACE TEMP TABLE oc AS SELECT e.event_id AS unit, TRUE AS activated,
          n < ? OR n > coalesce(?, 9223372036854775807) AS violated, CAST(n AS VARCHAR) || ' linked' AS detail
        FROM (SELECT e.event_id, count(DISTINCT eo.object_id) FILTER (WHERE o.object_type = ?) AS n
              FROM events e LEFT JOIN event_objects eo ON eo.log_id = e.log_id AND eo.event_id = e.event_id
              LEFT JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
              WHERE e.log_id = ? AND e.activity = ? GROUP BY 1) e""",
                 [rule.min, rule.max, rule.object_type, log_id, rule.activity])


def _typed_links(conn, log_id: str, name: str, object_type: str, activity: str | None = None) -> None:
    where = "AND e.activity = ?" if activity else ""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {name} AS SELECT DISTINCT eo.event_id, eo.object_id, e.ts, e.activity
        FROM event_objects eo JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
        JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
        WHERE eo.log_id = ? AND o.object_type = ? {where}""", [log_id, object_type] + ([activity] if activity else []))


def _pairs_within_budget(conn) -> bool:
    """Object pairs through shared events between ``lt`` and ``lu`` stay within the work budget."""
    pairs = conn.execute("""SELECT coalesce(sum(t.n * u.n), 0) FROM
          (SELECT event_id, count(*) AS n FROM lt GROUP BY 1) t
          JOIN (SELECT event_id, count(*) AS n FROM lu GROUP BY 1) u USING (event_id)""").fetchone()[0]
    return pairs <= MAX_RELATION_PAIRS


def _relations(conn) -> None:
    """``rel`` (object_id, related_id): objects of the two types that share an event, never an object with itself."""
    conn.execute("""CREATE OR REPLACE TEMP TABLE rel AS SELECT DISTINCT t.object_id, u.object_id AS related_id
        FROM lt t JOIN lu u ON u.event_id = t.event_id WHERE u.object_id <> t.object_id""")


def _object_cardinality(conn, log_id: str, rule: ObjectCardinality) -> bool:
    _typed_links(conn, log_id, "lt", rule.object_type, rule.via_activity)
    _typed_links(conn, log_id, "lu", rule.related_type, rule.via_activity)
    if not _pairs_within_budget(conn):
        return False
    _relations(conn)
    conn.execute("""CREATE OR REPLACE TEMP TABLE oc AS SELECT o.object_id AS unit, TRUE AS activated,
          n < ? OR n > coalesce(?, 9223372036854775807) AS violated, CAST(n AS VARCHAR) || ' related' AS detail
        FROM (SELECT o.object_id, count(r.related_id) AS n FROM objects o
              LEFT JOIN rel r ON r.object_id = o.object_id
              WHERE o.log_id = ? AND o.object_type = ? GROUP BY 1) o""", [rule.min, rule.max, log_id, rule.object_type])
    return True


def _synchronization(conn, log_id: str, rule: Synchronization) -> bool:
    _typed_links(conn, log_id, "lt", rule.object_type)
    _typed_links(conn, log_id, "lu", rule.related_type)
    if not _pairs_within_budget(conn):
        return False
    _relations(conn)
    # Each related object's earliest required event, in (time, event ID) order.
    conn.execute("""CREATE OR REPLACE TEMP TABLE req AS SELECT object_id, min(ts) AS ts,
          min(event_id) FILTER (WHERE ts = min_ts) AS event_id
        FROM (SELECT *, min(ts) OVER (PARTITION BY object_id) AS min_ts FROM lu WHERE activity = ?) GROUP BY 1""",
                 [rule.required_activity])
    # A required event counts when it precedes the trigger: earlier time, or the same time and a smaller event ID.
    conn.execute("""CREATE OR REPLACE TEMP TABLE oc AS SELECT unit, related > 0 AS activated,
          missing + later > 0 AS violated,
          CAST(missing AS VARCHAR) || ' related without it, ' || CAST(later AS VARCHAR) || ' only later' AS detail
        FROM (SELECT t.event_id || ' / ' || t.object_id AS unit, count(r.related_id) AS related,
                count(r.related_id) FILTER (WHERE q.object_id IS NULL) AS missing,
                count(r.related_id) FILTER (WHERE q.ts > t.ts OR (q.ts = t.ts AND q.event_id >= t.event_id)) AS later
              FROM lt t LEFT JOIN rel r ON r.object_id = t.object_id LEFT JOIN req q ON q.object_id = r.related_id
              WHERE t.activity = ? GROUP BY t.event_id, t.object_id)""", [rule.activity])
    return True


def _unit(rule) -> str:
    return {"event_cardinality": "events", "object_cardinality": "objects",
            "synchronization": "event_object_pairs"}[rule.template]


def evaluate(conn, log_id: str, rule) -> dict[str, Any]:
    known: dict[str, bool | None] = {}
    for key, value, sql in (
            ("activity_in_log", getattr(rule, "activity", None) or getattr(rule, "via_activity", None),
             "SELECT 1 FROM events WHERE log_id = ? AND activity = ? LIMIT 1"),
            ("required_activity_in_log", getattr(rule, "required_activity", None),
             "SELECT 1 FROM events WHERE log_id = ? AND activity = ? LIMIT 1"),
            ("object_type_in_log", rule.object_type, "SELECT 1 FROM objects WHERE log_id = ? AND object_type = ? LIMIT 1"),
            ("related_type_in_log", getattr(rule, "related_type", None),
             "SELECT 1 FROM objects WHERE log_id = ? AND object_type = ? LIMIT 1")):
        if value is not None:
            known[key] = conn.execute(sql, [log_id, value]).fetchone() is not None
    summary: dict[str, Any] = {"rule": rule.model_dump(exclude_none=True), "description": describe(rule),
                               "unit": _unit(rule), **known}
    if isinstance(rule, EventCardinality):
        _event_cardinality(conn, log_id, rule)
        complete = True
    elif isinstance(rule, ObjectCardinality):
        complete = _object_cardinality(conn, log_id, rule)
    else:
        complete = _synchronization(conn, log_id, rule)
    if not complete:
        return summary | {"complete": False, "unknown_reason": "relation_work_budget",
                          "limits": {"relation_pairs": MAX_RELATION_PAIRS}}
    units, activated, violated = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE activated), count(*) FILTER (WHERE violated) FROM oc").fetchone()
    examples = [{"unit": u, "detail": d} for u, d in conn.execute(
        "SELECT unit, detail FROM oc WHERE violated ORDER BY unit LIMIT ?", [MAX_EXAMPLES]).fetchall()]
    return summary | {
        "complete": True, "units": units, "activated": activated, "vacuously_satisfied": units - activated,
        "violating": violated, "satisfied": activated - violated,
        "violation_rate": violated / activated if activated else None,
        "violation_rate_ci95": list(_wilson(violated, activated, 1.959964)) if activated else None,
        "violating_examples": examples, "examples_truncated": violated > len(examples),
    }


def check(log_id: str, spec: Any) -> dict[str, Any]:
    rules = parse(spec)
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            results = [evaluate(conn, log_id, rule) for rule in rules.rules]
        finally:
            conn.close()
    return {
        "log": info, "results": results,
        "rules_with_violations": sum(1 for r in results if r.get("violating")),
        "rules_unknown": sum(1 for r in results if not r["complete"]),
        "ordering_basis": "timestamp_then_event_id",
        "interpretation": ("Rules check the recorded links between objects without flattening the log onto one case "
                           "notion. Objects are related when they share an event. Results describe this log, not "
                           "verified business relationships, audit evidence or legal compliance; a violation may "
                           "reflect missing events or links."),
        **workspace.DESCRIPTIVE_ONLY,
    }


__all__ = ["check", "describe", "parse"]
