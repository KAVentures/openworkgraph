"""Read-only, same-log, cardinality-aware joined business-attribute analysis (R9).

This intentionally does not guess a business join from temporal proximity.  A
case can inherit an attribute only from exactly one OTHER object co-observed
on its selected events.  Multiple related objects remain unresolved even if
their labels happen to match.
"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-linked-attribute-analysis.v1"
MAX_CASE_EVENT_ROWS = 2_000_000
MAX_EVENT_OBJECT_LINKS = 4_000_000
MAX_VALUE_LENGTH = 120


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    case_type: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    related_object_type: Annotated[str, Field(min_length=1, max_length=200)]
    attribute: Annotated[str, Field(min_length=1, max_length=120, pattern=r'^[^"\\\x00-\x1f]+$')]
    cohort: dict[str, Any] = Field(default_factory=dict)
    limit: StrictInt = Field(default=50, ge=1, le=100)


def parse(body: Any) -> _Spec:
    try:
        spec = _Spec.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "linked attribute analysis is invalid: " + ".".join(map(str, first["loc"])) + " " + first["msg"]
        ) from None
    if spec.case_type is not None and spec.case_type == spec.related_object_type:
        raise workspace.EventLogWorkspaceError("related_object_type must differ from case_type")
    return spec


def analyze(log_id: str, body: Any) -> dict[str, Any]:
    """Return grouped observed case durations by a unique co-observed object's attribute.

    No raw case IDs, object IDs, source rows, arbitrary SQL or provider access.
    An optional cohort applies to complete cases before the join and before
    computing denominators.  All user-provided SQL values are parameters.
    """
    spec = parse(body)

    def execute(conn):
        # The selected case notion is known only after _query has checked the
        # log; reject self-joins even when the caller omitted case_type.
        case_type = workspace._case_type(conn, log_id, spec.case_type)
        if case_type == spec.related_object_type:
            raise workspace.EventLogWorkspaceError("related_object_type must differ from case_type")
        if not conn.execute(
            "SELECT 1 FROM objects WHERE log_id = ? AND object_type = ? LIMIT 1",
            [log_id, spec.related_object_type],
        ).fetchone():
            raise workspace.EventLogWorkspaceError("related_object_type is not present in this event log")

        case_event_rows = conn.execute("SELECT count(*) FROM ce").fetchone()[0]
        if case_event_rows > MAX_CASE_EVENT_ROWS:
            raise workspace.EventLogWorkspaceError("linked attribute analysis exceeds selected case-event work budget")
        links_examined = conn.execute(
            """SELECT count(*) FROM event_objects eo WHERE eo.log_id = ?
               AND eo.event_id IN (SELECT DISTINCT event_id FROM ce)""", [log_id]
        ).fetchone()[0]
        if links_examined > MAX_EVENT_OBJECT_LINKS:
            raise workspace.EventLogWorkspaceError("linked attribute analysis exceeds selected event-object link work budget")

        conn.execute("""CREATE OR REPLACE TEMP TABLE ja_cases AS
            SELECT case_id, epoch(max(ts) - min(ts)) AS duration_seconds
            FROM ce GROUP BY case_id""")
        # Co-observed means SAME event, not shared clock proximity or a
        # supplied cross-object relationship.  DISTINCT defeats duplicated
        # event-object rows and repeated events within one case.
        conn.execute("""CREATE OR REPLACE TEMP TABLE ja_links AS
            SELECT DISTINCT ce.case_id, eo.object_id
            FROM (SELECT DISTINCT case_id, event_id FROM ce) ce
            JOIN event_objects eo ON eo.log_id = ? AND eo.event_id = ce.event_id
            JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
                AND o.object_type = ?
            WHERE eo.object_id <> ce.case_id""", [log_id, spec.related_object_type])
        # Multiple stored object definitions (e.g. contradictory appends)
        # must not be arbitrarily resolved with ANY_VALUE.
        path = '$."' + spec.attribute + '"'
        conn.execute("""CREATE OR REPLACE TEMP TABLE ja_attrs AS
            SELECT l.case_id, l.object_id,
                count(DISTINCT o.attributes) AS definitions,
                any_value(json_extract_string(o.attributes, ?)) AS value
            FROM ja_links l JOIN objects o ON o.log_id = ? AND o.object_id = l.object_id
                AND o.object_type = ?
            GROUP BY l.case_id, l.object_id""", [path, log_id, spec.related_object_type])
        conn.execute("""CREATE OR REPLACE TEMP TABLE ja_joined AS
            SELECT c.case_id, c.duration_seconds,
                coalesce(a.related_objects, 0) AS related_objects,
                coalesce(a.conflicting_definitions, 0) AS conflicting_definitions,
                a.value
            FROM ja_cases c LEFT JOIN (
                SELECT case_id, count(*) AS related_objects,
                    count(*) FILTER (WHERE definitions <> 1) AS conflicting_definitions,
                    any_value(value) AS value
                FROM ja_attrs GROUP BY case_id
            ) a USING (case_id)""")
        conn.execute("""CREATE OR REPLACE TEMP TABLE ja_status AS
            SELECT duration_seconds, value,
                CASE
                  WHEN related_objects = 0 THEN 'unlinked'
                  WHEN related_objects > 1 OR conflicting_definitions > 0 THEN 'ambiguous'
                  WHEN value IS NULL OR value = '' THEN 'missing_attribute'
                  WHEN length(value) > ? THEN 'oversize_attribute'
                  ELSE 'resolved'
                END AS status
            FROM ja_joined""", [MAX_VALUE_LENGTH])
        counts = dict(conn.execute(
            "SELECT status, count(*) FROM ja_status GROUP BY status"
        ).fetchall())
        status_counts = {name: int(counts.get(name, 0)) for name in
                         ("resolved", "unlinked", "ambiguous", "missing_attribute", "oversize_attribute")}
        rows = conn.execute("""SELECT value, count(*) AS cases,
                quantile_cont(duration_seconds, 0.5) AS median_duration_seconds
            FROM ja_status WHERE status = 'resolved'
            GROUP BY value ORDER BY cases DESC, value ASC LIMIT ?""", [spec.limit]).fetchall()
        groups = conn.execute("""SELECT count(*) FROM (
            SELECT value FROM ja_status WHERE status = 'resolved' GROUP BY value)""").fetchone()[0]
        visible = sum(int(n) for _, n, _ in rows)
        return {
            "format": FORMAT,
            "related_object_type": spec.related_object_type,
            "attribute": spec.attribute,
            "cases": sum(status_counts.values()),
            "status_counts": status_counts,
            "rows": [{"value": value, "cases": int(n),
                      "median_duration_seconds": float(duration) if duration is not None else None}
                     for value, n, duration in rows],
            "groups_total": int(groups),
            "groups_returned": len(rows),
            "groups_omitted": int(groups) - len(rows),
            "resolved_cases_omitted_by_group_limit": status_counts["resolved"] - visible,
            "limits": {"max_groups": spec.limit, "max_value_length": MAX_VALUE_LENGTH,
                       "max_case_event_rows": MAX_CASE_EVENT_ROWS,
                       "max_event_object_links": MAX_EVENT_OBJECT_LINKS},
            "work": {"selected_case_event_rows": int(case_event_rows),
                     "selected_event_object_links": int(links_examined)},
            "join_basis": "distinct_same_log_coobserved_event_object_links",
            "resolution": "exactly_one_related_object_and_one_stored_object_definition",
            "attribute_values_are_supplied_not_source_verified": True,
            "case_duration_is_observed_elapsed_not_verified_work_time": True,
        }

    return workspace._query_filtered(log_id, spec.case_type, spec.cohort, execute)
