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
MAX_RELATION_PAIRS = 2_000_000
MAX_SIGNATURE_LINKS = 1_000_000

# An event related to the same object under several qualifiers is still one step of its lifecycle.
_TYPED = """CREATE OR REPLACE TEMP TABLE te AS
  SELECT DISTINCT o.object_type, eo.object_id, e.event_id, e.activity, e.ts
  FROM event_objects eo
  JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
  JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
  WHERE eo.log_id = ?"""


def _pair_candidates(conn, table):
    # Count a join's directed cross-type rows without expanding object pairs.
    return int(conn.execute(f"""WITH counts AS (
        SELECT event_id, object_type, count(*)::HUGEINT AS n FROM {table} GROUP BY 1, 2),
        totals AS (SELECT sum(n) AS n, sum(n*n) AS squares FROM counts GROUP BY event_id)
        SELECT coalesce(sum(n*n-squares),0) FROM totals""").fetchone()[0])


def _relations(conn, types):
    import duckdb

    status = {'relations_calculation_complete': True, 'relations_unknown_reason': None,
              'relation_scope': 'displayed_object_types_with_observed_events', 'relation_pair_candidates': None,
              'relation_group_pair_candidates': None, 'relations_count_method': 'no_cross_type_links',
              'relation_limits': {'pair_candidates': MAX_RELATION_PAIRS, 'signature_input_links': MAX_SIGNATURE_LINKS}}
    try:
        return _bounded_relations(conn, types, status)
    except duckdb.OutOfMemoryException:
        return [], status | {'relations_calculation_complete': False,
                             'relations_unknown_reason': 'relation_resource_limit',
                             'relations_count_method': 'not_computed'}


def _bounded_relations(conn, types, status):
    if len(types) < 2:
        return [], status | {'relation_pair_candidates': 0}
    conn.execute("""CREATE TEMP TABLE relation_events AS SELECT object_type, object_id, event_id
        FROM te WHERE list_contains(?, object_type)""", [types])
    candidates = _pair_candidates(conn, 'relation_events')
    status['relation_pair_candidates'] = candidates
    if not candidates:
        return [], status
    if candidates <= MAX_RELATION_PAIRS:
        rows = conn.execute("""WITH pairs AS (
              SELECT DISTINCT a.object_type AS ta, a.object_id AS oa, b.object_type AS tb, b.object_id AS ob
              FROM relation_events a JOIN relation_events b ON a.event_id = b.event_id AND a.object_type <> b.object_type),
            per AS (SELECT ta, tb, oa, count(*) AS linked FROM pairs GROUP BY 1, 2, 3)
            SELECT ta, tb, count(*), sum(linked), avg(linked), max(linked)
            FROM per GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
        return rows, status | {'relations_count_method': 'direct_object_pairs'}
    links = conn.execute('SELECT count(*) FROM relation_events').fetchone()[0]
    if links <= MAX_SIGNATURE_LINKS:
        # Equal complete event-ID sets imply equal links, independent of timestamps.
        # Group on the actual lists, never on a possibly colliding digest.
        conn.execute("""CREATE TEMP TABLE relation_signatures AS SELECT object_type, event_ids,
            count(*) AS objects, row_number() OVER () AS signature_id FROM (
              SELECT object_type, object_id, list(event_id ORDER BY event_id) AS event_ids
              FROM relation_events GROUP BY 1, 2) GROUP BY 1, 2""")
        conn.execute("""CREATE TEMP TABLE signature_events AS
            SELECT signature_id, object_type, unnest(event_ids) AS event_id FROM relation_signatures""")
        grouped = _pair_candidates(conn, 'signature_events')
        status['relation_group_pair_candidates'] = grouped
        if grouped <= MAX_RELATION_PAIRS:
            rows = conn.execute("""WITH pairs AS (
                  SELECT DISTINCT a.signature_id AS aid, b.signature_id AS bid FROM signature_events a
                  JOIN signature_events b ON a.event_id=b.event_id AND a.object_type<>b.object_type),
                per AS (SELECT a.object_type AS ta, b.object_type AS tb, a.signature_id, a.objects AS n,
                    sum(b.objects)::HUGEINT AS linked FROM pairs
                  JOIN relation_signatures a ON a.signature_id=pairs.aid
                  JOIN relation_signatures b ON b.signature_id=pairs.bid GROUP BY 1, 2, 3, 4)
                SELECT ta, tb, sum(n), sum(n::HUGEINT*linked),
                    sum(n::HUGEINT*linked)::DOUBLE/sum(n), max(linked)
                FROM per GROUP BY 1, 2 ORDER BY 1, 2""").fetchall()
            return rows, status | {'relations_count_method': 'identical_event_set_groups'}
    return [], status | {'relations_calculation_complete': False,
                         'relations_unknown_reason': 'relation_pair_work_budget',
                         'relations_count_method': 'not_computed'}


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
            conn.execute("""CREATE TEMP TABLE edge_counts AS
                SELECT object_type, activity, nxt, count(*) AS occurrences,
                    count(DISTINCT object_id) AS cases, quantile_cont(gap, 0.5) AS median_gap
                FROM steps WHERE nxt IS NOT NULL GROUP BY 1, 2, 3""")
            result = []
            for object_type, objects, events in types:
                activities = conn.execute(f"""SELECT activity, count(*), count(DISTINCT object_id), count(*) OVER () FROM steps
                    WHERE object_type = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_ACTIVITIES_PER_TYPE}""",
                                          [object_type]).fetchall()
                kept = [a for a, _, _, _ in activities]
                edges = conn.execute(f"""SELECT activity, nxt, occurrences, cases, median_gap
                    FROM edge_counts WHERE object_type = ? AND list_contains(?, activity) AND list_contains(?, nxt)
                    ORDER BY 3 DESC, 1, 2 LIMIT {MAX_EDGES_PER_TYPE}""", [object_type, kept, kept]).fetchall()
                total_edges = conn.execute("SELECT count(*) FROM edge_counts WHERE object_type = ?", [object_type]).fetchone()[0]
                total_activities = activities[0][3] if activities else 0
                bounds = conn.execute("""SELECT kind, activity, count(*) FROM (
                      SELECT 'start' AS kind, activity FROM steps WHERE object_type = $t AND position = 1
                      UNION ALL SELECT 'end', activity FROM steps WHERE object_type = $t AND position = length)
                    GROUP BY 1, 2 ORDER BY 3 DESC, 2""", {"t": object_type}).fetchall()
                lifecycle = conn.execute("""SELECT quantile_cont(n, 0.5), max(n) FROM (
                    SELECT count(*) AS n FROM steps WHERE object_type = ? GROUP BY object_id)""", [object_type]).fetchone()
                result.append({
                    "object_type": object_type, "objects": objects, "events": events,
                    "median_events_per_object": lifecycle[0], "max_events_per_object": lifecycle[1],
                    "activities": [{"activity": a, "event_count": n, "case_count": o} for a, n, o, _ in activities],
                    "activity_types": total_activities, "transition_types": total_edges,
                    "activities_truncated": total_activities > len(activities),
                    "transitions_truncated": total_edges > len(edges),
                    "transitions": [{"from": a, "to": b, "transition_count": n, "case_count": o,
                                     "median_elapsed_gap_seconds": g} for a, b, n, o, g in edges],
                    "start_activities": {a: n for kind, a, n in bounds if kind == "start"},
                    "end_activities": {a: n for kind, a, n in bounds if kind == "end"},
                })
            relations, relation_status = _relations(conn, [t for t, _, _ in types])
            counts = {t: n for t, n, _ in types}
            shared = conn.execute("""SELECT activity, list(DISTINCT object_type ORDER BY object_type), count(DISTINCT event_id)
                FROM te GROUP BY activity HAVING count(DISTINCT object_type) > 1 ORDER BY 3 DESC, 1 LIMIT 50""").fetchall()
            type_count = conn.execute("SELECT count(DISTINCT object_type) FROM te").fetchone()[0]
            return {
                "log": info,
                "object_types": result,
                "object_type_count": type_count,
                "object_types_truncated": type_count > len(types),
                "ordering": "Timestamp, then event ID; equal timestamps do not verify business order.",
                "relations": [{"from_type": a, "to_type": b, "objects_with_link": n,
                               "share_of_from_objects": round(n / counts[a], 6) if counts.get(a) else None,
                               "linked_objects_total": links, "mean_linked_per_object": round(mean, 4),
                               "max_linked_per_object": top}
                              for a, b, n, links, mean, top in relations if a in counts and b in counts],
                "activities_touching_several_types": [{"activity": a, "object_types": t, "events": n}
                                                      for a, t, n in shared],
                "links_are_shared_events_not_verified_relationships": True,
                **relation_status,
                **workspace.DESCRIPTIVE_ONLY,
            }
        finally:
            conn.close()
