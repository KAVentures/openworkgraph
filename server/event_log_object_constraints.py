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
- ``supplied_object_cardinality``: distinct direct neighbors in explicitly supplied OCEL assertions,
  with direction and optional literal qualifier; includes zero-event objects and self-assertions.
  This static graph-shape check does not infer temporal synchronization or verified business facts.

Events are ordered by time, then event ID, the same tie-break as the rest of the workspace. A rule whose
trigger never occurs is satisfied vacuously and counted separately; a synchronization with no related
objects is too. Relating objects stops at a disclosed work budget instead of returning partial counts.
Results describe the recorded links of the imported log only: they are not verified business
relationships, audit evidence or legal compliance.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

import duckdb
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError, field_validator
from shared.event_log_formats import _exact_key,EventLogFormatError

from . import event_log_workspace as workspace
from .event_log_analytics import _wilson
from . import event_log_object_relationships as supplied_relationships

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


class SuppliedObjectCardinality(_Strict):
    model_config=ConfigDict(extra='forbid',strict=True)
    template:Literal['supplied_object_cardinality']
    object_type:Label
    related_type:Label
    direction:Literal['outgoing','incoming','both']='outgoing'
    qualifier:str|None=Field(default=None,max_length=80)
    min:Count=0
    max:Count|None=None

    @field_validator('object_type','related_type')
    @classmethod
    def literal_types(cls,value):
        if not _exact_key(value):raise ValueError('A literal object type is required')
        return value

    @field_validator('qualifier')
    @classmethod
    def literal_qualifier(cls,value):
        if value is not None:_exact_key(value,80)
        return value


Rule = Annotated[Union[EventCardinality, ObjectCardinality, Synchronization, SuppliedObjectCardinality], Field(discriminator="template")]


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
        if isinstance(rule, (EventCardinality, ObjectCardinality, SuppliedObjectCardinality)):
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
    if isinstance(rule, SuppliedObjectCardinality):
        qualifier=(' with exact qualifier '+repr(rule.qualifier)) if rule.qualifier is not None else ' across all qualifiers'
        focus=repr(rule.object_type) if rule.object_type.strip()!=rule.object_type else rule.object_type
        related=repr(rule.related_type) if rule.related_type.strip()!=rule.related_type else rule.related_type
        return (f'every {focus} has {_bounds(rule)} distinct {related} neighbors '
                f'in {rule.direction} supplied assertions{qualifier}')
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
    _cardinality_counts(conn,log_id,rule)
    return True


def _cardinality_counts(conn,log_id,rule):
    conn.execute("""CREATE OR REPLACE TEMP TABLE oc AS SELECT o.object_id AS unit, TRUE AS activated,
          n < ? OR n > coalesce(?, 9223372036854775807) AS violated, CAST(n AS VARCHAR) || ' related' AS detail
        FROM (SELECT o.object_id, count(r.related_id) AS n FROM objects o
              LEFT JOIN rel r ON r.object_id = o.object_id
              WHERE o.log_id = ? AND o.object_type = ? GROUP BY 1) o""", [rule.min, rule.max, log_id, rule.object_type])


def _supplied_cardinality(conn,log_id,rule):
    branches=[];params=[]
    for direction in (['outgoing','incoming'] if rule.direction=='both' else [rule.direction]):
        focus,related=('s','t') if direction=='outgoing' else ('t','s')
        exact=' AND r.qualifier=?' if rule.qualifier is not None else ''
        branches.append(f'''SELECT {focus}.object_id,{related}.object_id AS related_id
            FROM object_relationships r JOIN objects s ON s.log_id=r.log_id AND s.object_id=r.source_object_id
            JOIN objects t ON t.log_id=r.log_id AND t.object_id=r.target_object_id
            WHERE r.log_id=? AND {focus}.object_type=? AND {related}.object_type=?{exact}''')
        params.extend([log_id,rule.object_type,rule.related_type])
        if rule.qualifier is not None:params.append(rule.qualifier)
    pairs=' UNION ALL '.join(branches)
    # Bound oriented assertion candidates before materializing/deduplicating
    # neighbors. Qualifiers/reverse/self duplicates can overestimate work, never
    # understate it or become a partial exact result.
    candidates=conn.execute('SELECT count(*) FROM ('+pairs+') candidate',params).fetchone()[0]
    if candidates>MAX_RELATION_PAIRS:return False
    conn.execute('CREATE OR REPLACE TEMP TABLE rel AS SELECT DISTINCT * FROM ('+pairs+') candidate',params)
    _cardinality_counts(conn,log_id,rule)
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
            "synchronization": "event_object_pairs",'supplied_object_cardinality':'objects'}[rule.template]


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
    if isinstance(rule,SuppliedObjectCardinality):
        summary.update(relationship_basis='caller_supplied_ocel_object_relationships',
            relation_count_basis='distinct_neighbors_not_assertion_occurrences',independently_verified=False,
            work_budget_basis='oriented_supplied_assertion_candidates_before_neighbor_deduplication',
            violation_rate_basis='exact_retained_focus_objects_not_population_inference',
            business_compliance='unknown',source_completeness='unknown',relationship_validity='unknown',
            case_membership_inferred=False,transitive_relationships_inferred=False)
    if isinstance(rule, EventCardinality):
        _event_cardinality(conn, log_id, rule)
        complete = True
    elif isinstance(rule, ObjectCardinality):
        complete = _object_cardinality(conn, log_id, rule)
    elif isinstance(rule,SuppliedObjectCardinality):
        try:complete = _supplied_cardinality(conn,log_id,rule)
        except duckdb.OutOfMemoryException:
            return summary|{'complete':False,'unknown_reason':'analysis_memory_limit',
                'limits':{'relation_pairs':MAX_RELATION_PAIRS}}
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
        "violation_rate_ci95": (list(_wilson(violated, activated, 1.959964))
                                if activated and not isinstance(rule,SuppliedObjectCardinality) else None),
        "violating_examples": examples, "examples_truncated": violated > len(examples),
    }


def check(log_id: str, spec: Any) -> dict[str, Any]:
    rules = parse(spec)
    supplied=any(isinstance(rule,SuppliedObjectCardinality) for rule in rules.rules)
    basis={}
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            if supplied:
                try:basis=supplied_relationships.snapshot(conn,log_id)
                except EventLogFormatError as error:raise workspace.EventLogWorkspaceError(str(error)) from None
                except duckdb.OutOfMemoryException:
                    raise workspace.EventLogWorkspaceError('The supplied graph snapshot exceeds available analysis memory. No partial result was returned.') from None
            results = [evaluate(conn, log_id, rule) for rule in rules.rules]
        finally:
            conn.close()
    response = {
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
    if supplied:
        response['supplied_relationship_basis']=basis
        response['interpretation']=('Shared-event rules relate objects through recorded shared events. Supplied-relationship '
            'cardinality rules count distinct direct neighbors in the retained caller-supplied assertion set, including '
            'objects with no events. Missing/excess neighbors describe that retained set, not verified business '
            'compliance or source completeness. Static assertions do not establish historical validity, causality, '
            'case membership, temporal synchronization or execution authority.')
        if all(isinstance(rule,SuppliedObjectCardinality) for rule in rules.rules):
            response['ordering_basis']='not_used_for_supplied_graph_shape'
    return response


__all__ = ["check", "describe", "parse"]
