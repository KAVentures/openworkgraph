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
from .event_log_object_relationships import _revision

MAX_TYPES = 20
MAX_ATTRIBUTES = 20
MAX_TRANSITIONS = 10
MAX_TIMELINE = 200
MAX_ASOF_LINKS = 5_000_000      # event links examined to compare stored values with values at event time

_INITIAL = "TIMESTAMP '1970-01-01 00:00:00'"


def browse_objects(log_id: str, object_type: str, search: str = '', after: str | None = None,
                   limit: int = 25, revision: int | None = None) -> dict[str, Any]:
    """Browse literal IDs of a type, including objects without event links."""
    if (not isinstance(object_type, str) or not 1 <= len(object_type) <= 200 or
            not isinstance(search, str) or len(search) > 200 or
            (after is not None and (not isinstance(after, str) or not 1 <= len(after) <= 200)) or
            type(limit) is not int or not 1 <= limit <= 100 or
            (revision is not None and (type(revision) is not int or revision < 0))):
        raise workspace.EventLogWorkspaceError('invalid object browser request')
    if after is not None and revision is None:
        raise workspace.EventLogWorkspaceError('restart the object browser from the first page')
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            current = _revision(conn, log_id)
            if revision is not None and revision != current:
                raise workspace.EventLogWorkspaceError('the retained log has changed; restart the object browser')
            args = [log_id, object_type, search]
            total = conn.execute('''SELECT count(*) FROM objects
                WHERE log_id=? AND object_type=? AND contains(object_id, ?)''', args).fetchone()[0]
            where_after = ' AND object_id > ?' if after is not None else ''
            ids = [r[0] for r in conn.execute('''SELECT object_id FROM objects
                WHERE log_id=? AND object_type=? AND contains(object_id, ?)''' + where_after +
                ' ORDER BY object_id LIMIT ?', [*args, *([after] if after is not None else []), limit + 1]).fetchall()]
            more = len(ids) > limit
            ids = ids[:limit]
            links, values = {}, {}
            if ids:
                links = dict(conn.execute('''SELECT object_id, count(DISTINCT event_id) FROM event_objects
                    WHERE log_id=? AND object_id IN (SELECT unnest(?)) GROUP BY object_id''', [log_id, ids]).fetchall())
                values = dict(conn.execute('''SELECT object_id, count(*) FROM object_attribute_values
                    WHERE log_id=? AND object_id IN (SELECT unnest(?)) GROUP BY object_id''', [log_id, ids]).fetchall())
            return {'object_type': object_type, 'search': search, 'log_revision': current,
                    'total_objects': total, 'next_after': ids[-1] if more else None,
                    'objects': [{'object_id': obj, 'linked_events': links.get(obj, 0),
                                 'timed_values': values.get(obj, 0)} for obj in ids]}
        finally:
            conn.close()


def object_history(log_id: str, object_type: str, object_id: str, offset: int = 0,
                   limit: int = 50, revision: int | None = None) -> dict[str, Any]:
    """Page one literal object's retained timed values from a single log revision."""
    if (not isinstance(object_type, str) or not 1 <= len(object_type) <= 200 or
            not isinstance(object_id, str) or not 1 <= len(object_id) <= 200 or
            type(offset) is not int or not 0 <= offset <= 2_147_483_647 or
            type(limit) is not int or not 1 <= limit <= 100 or
            (revision is not None and (type(revision) is not int or revision < 0))):
        raise workspace.EventLogWorkspaceError('invalid object attribute history request')
    if offset and revision is None:
        raise workspace.EventLogWorkspaceError('restart object attribute history from the first page')
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            current = _revision(conn, log_id)
            if revision is not None and revision != current:
                raise workspace.EventLogWorkspaceError('the retained log has changed; restart the object attribute history')
            found = conn.execute('SELECT 1 FROM objects WHERE log_id=? AND object_id=? AND object_type=?',
                                 [log_id, object_id, object_type]).fetchone()
            if not found:
                raise workspace.EventLogWorkspaceError('the object was not found in the selected type')
            total = conn.execute('SELECT count(*) FROM object_attribute_values WHERE log_id=? AND object_id=?',
                                 [log_id, object_id]).fetchone()[0]
            rows = conn.execute('''SELECT name, valid_from, value FROM object_attribute_values
                WHERE log_id=? AND object_id=? ORDER BY valid_from, name, value LIMIT ? OFFSET ?''',
                                [log_id, object_id, limit, offset]).fetchall()
            return {'object_type': object_type, 'object_id': object_id, 'log_revision': current,
                    'total_values': total, 'offset': offset,
                    'next_offset': offset + len(rows) if offset + len(rows) < total else None,
                    'values': [{'name': name, 'valid_from': _iso(at), 'value': value} for name, at, value in rows]}
        finally:
            conn.close()


def object_events(log_id: str, object_type: str, object_id: str, offset: int = 0,
                  limit: int = 50, revision: int | None = None) -> dict[str, Any]:
    """Page raw recorded events linked to one object, grouping link qualifiers per event."""
    if (not isinstance(object_type, str) or not 1 <= len(object_type) <= 200 or
            not isinstance(object_id, str) or not 1 <= len(object_id) <= 200 or
            type(offset) is not int or not 0 <= offset <= 2_147_483_647 or
            type(limit) is not int or not 1 <= limit <= 100 or
            (revision is not None and (type(revision) is not int or revision < 0))):
        raise workspace.EventLogWorkspaceError('invalid object event history request')
    if offset and revision is None:
        raise workspace.EventLogWorkspaceError('restart object event history from the first page')
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            workspace._check_log(conn, log_id)
            current = _revision(conn, log_id)
            if revision is not None and revision != current:
                raise workspace.EventLogWorkspaceError('the retained log has changed; restart the object event history')
            found = conn.execute('SELECT 1 FROM objects WHERE log_id=? AND object_id=? AND object_type=?',
                                 [log_id, object_id, object_type]).fetchone()
            if not found:
                raise workspace.EventLogWorkspaceError('the object was not found in the selected type')
            total = conn.execute('''SELECT count(DISTINCT eo.event_id) FROM event_objects eo
                JOIN events e ON e.log_id=eo.log_id AND e.event_id=eo.event_id
                WHERE eo.log_id=? AND eo.object_id=?''', [log_id, object_id]).fetchone()[0]
            rows = conn.execute('''SELECT e.event_id, e.activity, e.ts, list(DISTINCT eo.qualifier ORDER BY eo.qualifier)
                FROM event_objects eo JOIN events e ON e.log_id=eo.log_id AND e.event_id=eo.event_id
                WHERE eo.log_id=? AND eo.object_id=? GROUP BY e.event_id, e.activity, e.ts
                ORDER BY e.ts, e.event_id LIMIT ? OFFSET ?''', [log_id, object_id, limit, offset]).fetchall()
            return {'object_type': object_type, 'object_id': object_id, 'log_revision': current,
                    'total_events': total, 'offset': offset,
                    'next_offset': offset + len(rows) if offset + len(rows) < total else None,
                    'events': [{'event_id': event, 'activity': activity, 'timestamp': _iso(at), 'qualifiers': qualifiers}
                               for event, activity, at, qualifiers in rows]}
        finally:
            conn.close()


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


__all__ = ["attribute_changes", "object_history", "browse_objects", "object_events"]
