"""Object-centric process maps for imported event logs.

For every object type: its own directly-follows map (each object is one
lifecycle), how lifecycles start and end, and how often objects of different
types are linked by shared events. Maps describe recorded events; they are
not approved routes or verified dependencies.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

MAX_TYPES = 20
MAX_ACTIVITIES_PER_TYPE = 64
MAX_EDGES_PER_TYPE = 400

_TYPED = """CREATE OR REPLACE TEMP TABLE te AS
  SELECT o.object_type, eo.object_id, eo.qualifier, e.event_id, e.activity, e.ts
  FROM event_objects eo
  JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
  JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
  WHERE eo.log_id = ?"""


def object_graph(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            conn.execute(_TYPED, [log_id])
            types = conn.execute(f"""SELECT object_type, count(DISTINCT object_id), count(DISTINCT event_id)
                FROM te GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_TYPES}""").fetchall()
            conn.execute("""CREATE OR REPLACE TEMP TABLE steps AS SELECT object_type, object_id, activity, ts, event_id,
                  lead(activity) OVER w AS nxt, date_diff('second', ts, lead(ts) OVER w) AS gap,
                  row_number() OVER w AS position, count(*) OVER (PARTITION BY object_type, object_id) AS length
                FROM te WINDOW w AS (PARTITION BY object_type, object_id ORDER BY ts, event_id)""")
            result = []
            for object_type, objects, events in types:
                activities = conn.execute(f"""SELECT activity, count(*), count(DISTINCT object_id) FROM steps
                    WHERE object_type = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_ACTIVITIES_PER_TYPE}""",
                                          [object_type]).fetchall()
                kept = [a for a, _, _ in activities]
                edges = conn.execute(f"""SELECT activity, nxt, count(*), count(DISTINCT object_id), quantile_cont(gap, 0.5)
                    FROM steps WHERE object_type = ? AND nxt IS NOT NULL AND list_contains(?, activity) AND list_contains(?, nxt)
                    GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2 LIMIT {MAX_EDGES_PER_TYPE}""", [object_type, kept, kept]).fetchall()
                bounds = conn.execute("""SELECT kind, activity, count(*) FROM (
                      SELECT 'start' AS kind, activity FROM steps WHERE object_type = $t AND position = 1
                      UNION ALL SELECT 'end', activity FROM steps WHERE object_type = $t AND position = length)
                    GROUP BY 1, 2 ORDER BY 3 DESC, 2""", {"t": object_type}).fetchall()
                lifecycle = conn.execute("""SELECT quantile_cont(n, 0.5), max(n) FROM (
                    SELECT count(*) AS n FROM steps WHERE object_type = ? GROUP BY object_id)""", [object_type]).fetchone()
                result.append({
                    "object_type": object_type, "objects": objects, "events": events,
                    "median_events_per_object": lifecycle[0], "max_events_per_object": lifecycle[1],
                    "activities": [{"activity": a, "event_count": n, "case_count": o} for a, n, o in activities],
                    "activities_truncated": len(activities) == MAX_ACTIVITIES_PER_TYPE,
                    "transitions": [{"from": a, "to": b, "transition_count": n, "case_count": o,
                                     "median_elapsed_gap_seconds": g} for a, b, n, o, g in edges],
                    "start_activities": {a: n for kind, a, n in bounds if kind == "start"},
                    "end_activities": {a: n for kind, a, n in bounds if kind == "end"},
                })
            relations = conn.execute("""WITH pairs AS (
                  SELECT DISTINCT a.object_type AS ta, a.object_id AS oa, b.object_type AS tb, b.object_id AS ob
                  FROM te a JOIN te b ON a.event_id = b.event_id AND a.object_type <> b.object_type),
                per AS (SELECT ta, tb, oa, count(*) AS linked FROM pairs GROUP BY 1, 2, 3)
                SELECT per.ta, per.tb, count(*) AS objects_with_link, sum(linked) AS links,
                       avg(linked), max(linked)
                FROM per GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
            counts = {t: n for t, n, _ in types}
            shared = conn.execute("""SELECT activity, list(DISTINCT object_type ORDER BY object_type), count(DISTINCT event_id)
                FROM te GROUP BY activity HAVING count(DISTINCT object_type) > 1 ORDER BY 3 DESC, 1 LIMIT 50""").fetchall()
            return {
                "log": info,
                "object_types": result,
                "object_types_truncated": len(types) == MAX_TYPES,
                "relations": [{"from_type": a, "to_type": b, "objects_with_link": n,
                               "share_of_from_objects": round(n / counts[a], 6) if counts.get(a) else None,
                               "linked_objects_total": links, "mean_linked_per_object": round(mean, 4),
                               "max_linked_per_object": top}
                              for a, b, n, links, mean, top in relations if a in counts and b in counts],
                "activities_touching_several_types": [{"activity": a, "object_types": t, "events": n}
                                                      for a, t, n in shared],
                "links_are_shared_events_not_verified_relationships": True,
                **workspace.DESCRIPTIVE_ONLY,
            }
        finally:
            conn.close()
