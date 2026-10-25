"""How object attributes change over time in an object-centric log (#370 R24).

OCEL 2.0 gives every object attribute value a time: a value holds from its time until the next value
of the same attribute. The workspace keeps the first listed value per attribute on the object (that is
what case attributes, filters and analyses use) and, separately, every timed value. For each object type
and attribute this reports how often the value changes, the most frequent changes, and what reading the
single stored value does to the events: how many event links happened while another value held, or
before any value was recorded. A value recorded at an event's exact time applies to that event. Values
for 1970-01-01T00:00:00Z are the OCEL convention for an initial value without a known time.

Counts describe the recorded values; they do not verify when the business change really happened.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

MAX_TYPES = 20
MAX_ATTRIBUTES = 20
MAX_TRANSITIONS = 10
MAX_TIMELINE = 200
MAX_ASOF_LINKS = 5_000_000      # event links examined to compare stored values with values at event time

_INITIAL = "TIMESTAMP '1970-01-01 00:00:00'"


def attribute_changes(log_id: str, object_type: str | None = None, object_id: str | None = None) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            # One row per (object, attribute, time); several values at one time are ambiguous (value NULL).
            conn.execute("""CREATE OR REPLACE TEMP TABLE ah AS SELECT v.object_id, o.object_type, v.name, v.valid_from,
                  CASE WHEN count(DISTINCT v.value) = 1 THEN any_value(v.value) END AS value,
                  count(DISTINCT v.value) > 1 AS ambiguous
                FROM object_attribute_values v JOIN objects o ON o.log_id = v.log_id AND o.object_id = v.object_id
                WHERE v.log_id = ? GROUP BY 1, 2, 3, 4""", [log_id])
            if object_type is not None:
                conn.execute("DELETE FROM ah WHERE object_type <> ?", [object_type])
            type_total = conn.execute("SELECT count(DISTINCT object_type) FROM ah").fetchone()[0]
            types = [r[0] for r in conn.execute(f"""SELECT object_type FROM ah GROUP BY 1
                ORDER BY count(DISTINCT object_id) DESC, 1 LIMIT {MAX_TYPES}""").fetchall()]
            conn.execute("""CREATE OR REPLACE TEMP TABLE ac AS SELECT object_id, object_type, name, valid_from, value,
                  ambiguous, lag(value) OVER w AS previous, lag(ambiguous) OVER w AS previous_ambiguous,
                  row_number() OVER w AS n
                FROM ah WINDOW w AS (PARTITION BY object_id, name ORDER BY valid_from)""")
            links = conn.execute("""SELECT count(*) FROM event_objects eo
                JOIN (SELECT DISTINCT object_id, name FROM ah) a ON a.object_id = eo.object_id
                WHERE eo.log_id = ?""", [log_id]).fetchone()[0]
            compared = links <= MAX_ASOF_LINKS
            if compared:
                _event_links(conn, log_id)
            result = [_one_type(conn, t, compared) for t in types]
            out = {
                "log": info, "object_types": result, "object_type_count": type_total,
                "object_types_truncated": type_total > len(types),
                "stored_value_comparison_complete": compared,
                "stored_value_comparison_unknown_reason": None if compared else "event_link_work_budget",
                "limits": {"event_links_examined": MAX_ASOF_LINKS, "attributes_per_type": MAX_ATTRIBUTES,
                           "transitions_listed": MAX_TRANSITIONS},
                "event_links_with_timed_attributes": int(links),
                "interpretation": (
                    "Case attributes, filters and analyses read one stored value per object attribute. Where an "
                    "attribute changes, events that happened while another value held are described with the "
                    "stored value. A value recorded at an event's time applies to that event; a 1970-01-01 value "
                    "is an initial value without a known time. Counts describe recorded values, not verified "
                    "business changes."),
                **workspace.DESCRIPTIVE_ONLY,
            }
            if object_id is not None:
                out["object"] = _timeline(conn, log_id, object_id)
            return out
        finally:
            conn.close()


def _event_links(conn, log_id: str) -> None:
    """Per event link and attribute: the value at the event's time and the object's stored value."""
    conn.execute("""CREATE OR REPLACE TEMP TABLE astored AS SELECT o.object_id, j.key AS name,
          json_extract_string(j.value, '$') AS stored
        FROM objects o, json_each(o.attributes) j WHERE o.log_id = ?
          AND o.object_id IN (SELECT DISTINCT object_id FROM ah)""", [log_id])
    conn.execute("""CREATE OR REPLACE TEMP TABLE al AS SELECT DISTINCT eo.event_id, eo.object_id, a.object_type, a.name, e.ts
        FROM event_objects eo JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
        JOIN (SELECT DISTINCT object_id, object_type, name FROM ah) a ON a.object_id = eo.object_id
        WHERE eo.log_id = ?""", [log_id])
    conn.execute("""CREATE OR REPLACE TEMP TABLE aasof AS SELECT l.object_type, l.name, l.object_id,
          h.valid_from IS NULL AS before_first, coalesce(h.ambiguous, false) AS ambiguous,
          h.value AS value_then, s.stored
        FROM al l ASOF LEFT JOIN ah h ON h.object_id = l.object_id AND h.name = l.name AND l.ts >= h.valid_from
        LEFT JOIN astored s ON s.object_id = l.object_id AND s.name = l.name""")


def _one_type(conn, t: str, compared: bool) -> dict[str, Any]:
    objects = conn.execute("SELECT count(DISTINCT object_id) FROM ah WHERE object_type = ?", [t]).fetchone()[0]
    names = conn.execute(f"""SELECT name, count(DISTINCT object_id) FROM ah WHERE object_type = ? GROUP BY 1
        ORDER BY 2 DESC, 1 LIMIT {MAX_ATTRIBUTES + 1}""", [t]).fetchall()
    attributes = []
    for name, with_values in names[:MAX_ATTRIBUTES]:
        p = [t, name]
        values, initial, ambiguous, changing, changes, first, last = conn.execute(f"""SELECT count(*),
              count(*) FILTER (WHERE valid_from = {_INITIAL}), count(*) FILTER (WHERE ambiguous),
              count(DISTINCT object_id) FILTER (WHERE n > 1 AND (ambiguous OR previous_ambiguous
                OR value IS DISTINCT FROM previous)),
              count(*) FILTER (WHERE n > 1 AND (ambiguous OR previous_ambiguous OR value IS DISTINCT FROM previous)),
              min(valid_from) FILTER (WHERE valid_from <> {_INITIAL}), max(valid_from) FILTER (WHERE valid_from <> {_INITIAL})
            FROM ac WHERE object_type = ? AND name = ?""", p).fetchone()
        transitions = conn.execute(f"""SELECT previous, value, count(*) FROM ac WHERE object_type = ? AND name = ?
            AND n > 1 AND NOT ambiguous AND NOT previous_ambiguous AND value IS DISTINCT FROM previous
            GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT {MAX_TRANSITIONS + 1}""", p).fetchall()
        item: dict[str, Any] = {
            "name": name, "objects_with_values": with_values, "timed_values": values,
            "initial_values_without_known_time": initial, "times_with_several_values": ambiguous,
            "objects_whose_value_changes": changing, "value_changes": changes,
            "first_change_or_value_at": _iso(first), "last_change_or_value_at": _iso(last),
            "most_frequent_changes": [{"from": a, "to": b, "changes": c} for a, b, c in transitions[:MAX_TRANSITIONS]],
            "changes_truncated": len(transitions) > MAX_TRANSITIONS,
        }
        if compared:
            links, before, unclear, differs = conn.execute("""SELECT count(*), count(*) FILTER (WHERE before_first),
                  count(*) FILTER (WHERE ambiguous), count(*) FILTER (WHERE NOT before_first AND NOT ambiguous
                    AND value_then IS DISTINCT FROM stored)
                FROM aasof WHERE object_type = ? AND name = ?""", p).fetchone()
            item["event_links"] = {"examined": links, "before_first_value": before,
                                   "at_a_time_with_several_values": unclear,
                                   "where_stored_value_differs_from_value_then": differs}
        attributes.append(item)
    return {"object_type": t, "objects_with_timed_attributes": objects, "attributes": attributes,
            "attributes_truncated": len(names) > MAX_ATTRIBUTES}


def _timeline(conn, log_id: str, object_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT object_type FROM objects WHERE log_id = ? AND object_id = ?", [log_id, object_id]).fetchone()
    if not row:
        raise workspace.EventLogWorkspaceError("unknown object in this log")
    values = conn.execute(f"""SELECT name, valid_from, value FROM object_attribute_values WHERE log_id = ? AND object_id = ?
        ORDER BY valid_from, name, value LIMIT {MAX_TIMELINE + 1}""", [log_id, object_id]).fetchall()
    events = conn.execute("""SELECT count(DISTINCT event_id) FROM event_objects WHERE log_id = ? AND object_id = ?""",
                          [log_id, object_id]).fetchone()[0]
    return {"object_id": object_id, "object_type": row[0], "linked_events": events,
            "values": [{"name": n, "valid_from": _iso(at), "value": v} for n, at, v in values[:MAX_TIMELINE]],
            "values_truncated": len(values) > MAX_TIMELINE}


def _iso(stamp) -> str | None:
    return None if stamp is None else stamp.isoformat() + "Z"


__all__ = ["attribute_changes"]
