"""What flattening an object-centric log onto one object type does to it (#370 R24).

Every case-notion analysis in the workspace flattens the log onto one object type: each object of
that type becomes a case and gets a copy of every event it is linked to. For each object type this
reports the three ways that changes the evidence, so case counts, event counts and cycle times are
read with the right caveats:

- deficiency: events linked to no object of the type disappear from the flattened log;
- convergence: an event linked to several objects of the type is copied into each of their cases,
  so event counts and activity frequencies are inflated;
- divergence: the same activity repeats within one case because it was done once per object of
  another type (for example once per item of an order); it then looks like rework or a loop.

It also reports how events are qualified per object type, and objects whose qualifier changes.
Counts describe the recorded links; they do not verify business relationships.
"""
from __future__ import annotations

from typing import Any

from . import event_log_workspace as workspace

MAX_TYPES = 20
MAX_LISTED = 20
MAX_DIVERGENCE_ROWS = 5_000_000      # links examined to explain repetitions by other object types

_LINKS = """CREATE OR REPLACE TEMP TABLE dl AS
  SELECT DISTINCT o.object_type, eo.object_id, e.event_id, e.activity
  FROM event_objects eo
  JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
  JOIN events e ON e.log_id = eo.log_id AND e.event_id = eo.event_id
  WHERE eo.log_id = ?"""


def flattening(log_id: str) -> dict[str, Any]:
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            info = workspace._check_log(conn, log_id)
            conn.execute(_LINKS, [log_id])
            total_events = conn.execute("SELECT count(*) FROM events WHERE log_id = ?", [log_id]).fetchone()[0]
            type_total = conn.execute("SELECT count(DISTINCT object_type) FROM dl").fetchone()[0]
            types = [r[0] for r in conn.execute(f"""SELECT object_type FROM dl GROUP BY 1
                ORDER BY count(DISTINCT object_id) DESC, 1 LIMIT {MAX_TYPES}""").fetchall()]
            # Per event and type: how many objects of the type it is linked to.
            conn.execute("""CREATE OR REPLACE TEMP TABLE de AS SELECT object_type, event_id, activity,
                  count(*) AS objects FROM dl GROUP BY 1, 2, 3""")
            conn.execute("""CREATE OR REPLACE TEMP TABLE dr AS SELECT object_type, object_id, activity,
                  count(*) AS repetitions FROM dl GROUP BY 1, 2, 3 HAVING count(*) > 1""")
            explained, divergence_status = _divergence(conn)
            result = [_one_type(conn, log_id, t, total_events, explained.get(t, {})) for t in types]
            return {
                "log": info, "events": total_events, "object_types": result,
                "object_type_count": type_total, "object_types_truncated": type_total > len(types),
                **divergence_status,
                "interpretation": (
                    "Case analyses flatten the log onto the chosen object type. Events linked to none of its objects "
                    "are left out, events linked to several are counted once per object, and an activity repeated "
                    "once per object of another type looks like rework. Choose the case notion with these counts in "
                    "view; they describe recorded links, not verified business relationships."),
                **workspace.DESCRIPTIVE_ONLY,
            }
        finally:
            conn.close()


def _divergence(conn) -> tuple[dict[str, dict[str, list[dict[str, Any]]]], dict[str, Any]]:
    """For repeated (object, activity) groups: which other object types differ between the repetitions."""
    status = {"divergence_explanation_complete": True, "divergence_explanation_unknown_reason": None,
              "divergence_limits": {"links_examined": MAX_DIVERGENCE_ROWS}}
    conn.execute("""CREATE OR REPLACE TEMP TABLE dre AS SELECT r.object_type, r.object_id, r.activity, l.event_id
        FROM dr r JOIN dl l USING (object_type, object_id, activity)""")
    # Work budget first: every repeated event's links to objects of other types.
    candidates = conn.execute("""SELECT count(*) FROM dre d
        JOIN dl o ON o.event_id = d.event_id AND o.object_type <> d.object_type""").fetchone()[0]
    status["divergence_links_examined"] = int(candidates)
    if candidates > MAX_DIVERGENCE_ROWS:
        return {}, status | {"divergence_explanation_complete": False,
                             "divergence_explanation_unknown_reason": "divergence_work_budget"}
    rows = conn.execute("""WITH per_group AS (
          SELECT d.object_type, d.object_id, d.activity, o.object_type AS other,
                 count(DISTINCT d.event_id) AS repetitions_linked, count(DISTINCT o.object_id) AS distinct_others
          FROM dre d JOIN dl o ON o.event_id = d.event_id AND o.object_type <> d.object_type
          GROUP BY 1, 2, 3, 4)
        SELECT object_type, activity, other, count(*) AS groups,
               count(*) FILTER (WHERE distinct_others > 1) AS groups_with_different_others,
               count(*) FILTER (WHERE distinct_others >= repetitions_linked AND distinct_others > 1) AS one_other_per_repetition
        FROM per_group GROUP BY 1, 2, 3""").fetchall()
    explained: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for t, activity, other, groups, different, one_each in rows:
        explained.setdefault(t, {}).setdefault(activity, []).append(
            {"object_type": other, "repeated_groups_linked": groups, "groups_with_different_objects": different,
             "groups_with_one_object_per_repetition": one_each})
    return explained, status


def _one_type(conn, log_id: str, t: str, total_events: int, explained: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    objects, events, rows, convergent, max_per_event = conn.execute("""SELECT
          (SELECT count(DISTINCT object_id) FROM dl WHERE object_type = $t),
          count(*), sum(objects), count(*) FILTER (WHERE objects > 1), max(objects)
        FROM de WHERE object_type = $t""", {"t": t}).fetchone()
    convergent_activities = conn.execute(f"""SELECT activity, count(*), count(*) FILTER (WHERE objects > 1),
          sum(objects), max(objects) FROM de WHERE object_type = ? GROUP BY 1
        HAVING count(*) FILTER (WHERE objects > 1) > 0 ORDER BY 3 DESC, 1 LIMIT {MAX_LISTED}""", [t]).fetchall()
    missing = conn.execute(f"""SELECT e.activity, count(*) FROM events e
        WHERE e.log_id = ? AND NOT EXISTS (SELECT 1 FROM de WHERE de.object_type = ? AND de.event_id = e.event_id)
        GROUP BY 1 ORDER BY 2 DESC, 1""", [log_id, t]).fetchall()
    divergent = conn.execute(f"""SELECT activity, count(*), sum(repetitions), max(repetitions)
        FROM dr WHERE object_type = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_LISTED + 1}""", [t]).fetchall()
    qualifiers = conn.execute(f"""SELECT eo.qualifier, count(*), count(DISTINCT eo.event_id) FROM event_objects eo
        JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
        WHERE eo.log_id = ? AND o.object_type = ? GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_LISTED}""", [log_id, t]).fetchall()
    changing = conn.execute("""SELECT count(*) FROM (SELECT eo.object_id FROM event_objects eo
        JOIN objects o ON o.log_id = eo.log_id AND o.object_id = eo.object_id
        WHERE eo.log_id = ? AND o.object_type = ? GROUP BY 1 HAVING count(DISTINCT eo.qualifier) > 1)""",
                            [log_id, t]).fetchone()[0]
    without = total_events - events
    return {
        "object_type": t, "objects": objects, "events_linked": events,
        "deficiency": {"events_left_out": without, "share_of_log_events": round(without / total_events, 6) if total_events else 0,
                       "by_activity": [{"activity": a, "events": n} for a, n in missing[:MAX_LISTED]],
                       "activities_truncated": len(missing) > MAX_LISTED},
        "convergence": {"convergent_events": convergent, "flattened_event_rows": int(rows or 0),
                        "extra_event_rows": int((rows or 0) - events), "max_objects_per_event": max_per_event or 0,
                        "duplication_factor": round(rows / events, 6) if events else None,
                        "by_activity": [{"activity": a, "events": n, "convergent_events": c, "flattened_rows": r,
                                         "max_objects_per_event": m} for a, n, c, r, m in convergent_activities]},
        "divergence": {"by_activity": [{"activity": a, "objects_with_repeats": n, "repetitions": int(r),
                                        "max_repetitions_per_object": m,
                                        "repetitions_differ_by": sorted(explained.get(a, []),
                                                                        key=lambda x: (-x["groups_with_different_objects"], x["object_type"]))}
                                       for a, n, r, m in divergent[:MAX_LISTED]],
                       "activities_truncated": len(divergent) > MAX_LISTED},
        "qualifiers": {"by_qualifier": [{"qualifier": q, "links": n, "events": e} for q, n, e in qualifiers],
                       "objects_with_several_qualifiers": changing},
    }


__all__ = ["flattening"]
