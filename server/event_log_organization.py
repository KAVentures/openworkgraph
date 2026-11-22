"""Organizational mining over saved event logs (roadmap #326 F1).

Who hands work to whom, who works on the same cases, and which resources
perform which activities: the handover-of-work and working-together views of
process mining, computed from the imported log's resource column.

These are flow descriptions, not people analytics:
- resources are pseudonymous by default ("Resource 01"); names appear only when
  the local user asks for them;
- no per-person duration, speed, wait, ranking or performance score is
  computed, including on handover edges;
- counts describe the imported log, not who is responsible for an outcome.
"""
from __future__ import annotations

from typing import Any

from . import event_log_cohorts as cohorts
from .event_log_cases import _integer, _spec, _text
from . import event_log_workspace as workspace

MAX_RESOURCES = 200
MAX_EDGES = 500
MAX_MATRIX_ACTIVITIES = 30
MAX_MATRIX_RESOURCES = 30
# Cases touched by more resources than this are excluded from the
# working-together pairs (quadratic) and reported, never silently clipped.
MAX_RESOURCES_PER_CASE = 50
_NAMES = {"pseudonymous", "shown"}


_HANDOVERS = """SELECT case_id, resource AS source, nxt AS target, back AS returned FROM (
    SELECT case_id, resource, lead(resource) OVER w AS nxt, lead(resource, 2) OVER w AS back
    FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
    WHERE resource <> '' AND nxt <> '' AND resource <> nxt"""
_CROWDED = """SELECT case_id FROM ce WHERE resource <> '' GROUP BY case_id
    HAVING count(DISTINCT resource) > ?"""
_SHARED_RESOURCES = f"""SELECT DISTINCT case_id, resource FROM ce
    WHERE resource <> '' AND case_id NOT IN ({_CROWDED})"""


def relationship_cases(log_id: str, spec: Any, case_type: str | None = None) -> dict[str, Any]:
    spec = _spec(spec, {"kind", "resources", "cohort", "after", "limit", "names"})
    if spec.get("names") != "shown":
        raise workspace.EventLogWorkspaceError("show resource names to review relationship cases")
    kind = spec.get("kind")
    if kind not in ("handover", "return", "shared"):
        raise workspace.EventLogWorkspaceError("choose a handover, return handover or shared cases")
    pair = spec.get("resources")
    if (not isinstance(pair, list) or len(pair) != 2
            or any(not isinstance(label, str) or not 0 < len(label) <= 200 for label in pair)
            or pair[0] == pair[1]):
        raise workspace.EventLogWorkspaceError("choose two distinct resource labels")
    source, target = pair
    after = _text(spec["after"]) if spec.get("after") is not None else None
    limit = _integer(spec.get("limit", 30), 100)
    cohort = cohorts.parse_cohort(spec.get("cohort"))

    def run(conn):
        cohorts.restrict(conn, cohort, log_id)
        if kind == "shared":
            sql = f"""SELECT case_id, 1 AS matches FROM ({_SHARED_RESOURCES})
                      WHERE resource IN (?, ?) GROUP BY case_id HAVING count(*) = 2"""
            args = [MAX_RESOURCES_PER_CASE, source, target]
        else:
            returned = " AND returned = source" if kind == "return" else ""
            sql = f"""SELECT case_id, count(*) AS matches FROM ({_HANDOVERS})
                      WHERE source = ? AND target = ?{returned} GROUP BY case_id"""
            args = [source, target]
        conn.execute(f"CREATE TEMP TABLE org_case_matches AS {sql}", args)
        total = conn.execute("SELECT count(*) FROM org_case_matches").fetchone()[0]
        condition, params = (" WHERE case_id > ?", [after]) if after is not None else ("", [])
        rows = conn.execute("SELECT case_id, matches FROM org_case_matches" + condition +
                            " ORDER BY case_id LIMIT ?", [*params, limit + 1]).fetchall()
        return {"selected_cases": total, "cases": [{"case_id": key, "matches": n} for key, n in rows[:limit]],
                "next_after": rows[limit - 1][0] if len(rows) > limit else None}
    return workspace._query(log_id, case_type, run)


def organization(log_id: str, case_type: str | None = None, *, names: str = "pseudonymous",
                 cohort: dict[str, Any] | None = None, automation_resources: list[str] | None = None,
                 limit: int = 100) -> dict[str, Any]:
    if names not in _NAMES:
        raise workspace.EventLogWorkspaceError("names must be pseudonymous or shown")
    limit = max(1, min(int(limit), MAX_EDGES))
    chosen = cohorts.parse_cohort(cohort)
    automation = {str(r) for r in (automation_resources or []) if r}
    if len(automation) > 50 or any(len(r) > 200 for r in automation):
        raise workspace.EventLogWorkspaceError("too many or too long automation resource names")

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        total_events, with_resource, cases = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE resource <> ''), count(DISTINCT case_id) FROM ce").fetchone()
        resources = conn.execute("""SELECT resource, count(*) AS events, count(DISTINCT case_id) AS cases,
              count(DISTINCT activity) AS activities FROM ce WHERE resource <> ''
            GROUP BY resource ORDER BY events DESC, resource LIMIT ?""", [MAX_RESOURCES + 1]).fetchall()
        truncated = len(resources) > MAX_RESOURCES
        resources = resources[:MAX_RESOURCES]
        label = {}
        for index, (name, *_rest) in enumerate(resources, start=1):
            label[name] = name if names == "shown" else f"Resource {index:02d}"

        other = "Other resources"
        while other in label.values():
            other += " (grouped)"
        conn.execute("""CREATE TEMP TABLE org_labels AS SELECT unnest(?::VARCHAR[]) AS resource,
                     unnest(?::VARCHAR[]) AS label""", [list(label), list(label.values())])
        conn.execute(f"CREATE TEMP TABLE org_handover_events AS {_HANDOVERS}")
        handover_cases = conn.execute("SELECT count(DISTINCT case_id) FROM org_handover_events").fetchone()[0]

        def transfers(only_returns: bool):
            returned = "WHERE returned = source" if only_returns else ""
            return conn.execute(f"""WITH marked AS (
                  SELECT case_id, coalesce(a.label, ?) AS src, coalesce(b.label, ?) AS dst
                  FROM org_handover_events LEFT JOIN org_labels a ON a.resource = source
                  LEFT JOIN org_labels b ON b.resource = target {returned})
                SELECT src, dst, count(*) AS n, count(DISTINCT case_id) FROM marked
                WHERE src <> dst GROUP BY src, dst ORDER BY n DESC, src, dst LIMIT ?""", [other, other, limit]).fetchall()

        handovers = transfers(False)
        subcontracting = transfers(True)
        crowded = conn.execute(f"SELECT count(*) FROM ({_CROWDED})", [MAX_RESOURCES_PER_CASE]).fetchone()[0]
        # Collapse unlisted resources within each case before joining pairs so
        # the same case contributes once even when multiple members are grouped.
        together = conn.execute(f"""WITH pr AS (
              SELECT DISTINCT case_id, coalesce(label, ?) AS label FROM ({_SHARED_RESOURCES}) members
              LEFT JOIN org_labels ON members.resource = org_labels.resource)
            SELECT a.label, b.label, count(*) AS cases FROM pr a JOIN pr b
              ON a.case_id = b.case_id AND a.label < b.label
            GROUP BY a.label, b.label ORDER BY cases DESC, a.label, b.label LIMIT ?""",
            [other, MAX_RESOURCES_PER_CASE, limit]).fetchall()
        top_activities = [row[0] for row in conn.execute("""SELECT activity FROM ce WHERE resource <> ''
            GROUP BY activity ORDER BY count(*) DESC, activity LIMIT ?""", [MAX_MATRIX_ACTIVITIES]).fetchall()]
        matrix_resources = [row[0] for row in resources[:MAX_MATRIX_RESOURCES]]
        cells = conn.execute("""SELECT activity, resource, count(*) FROM ce
            WHERE activity IN (SELECT unnest(?::VARCHAR[])) AND resource IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY activity, resource""", [top_activities, matrix_resources]).fetchall()
        lookup = {(a, r): n for a, r, n in cells}
        return {
            "resources": [{
                "resource": label[name], "events": events, "cases": n_cases, "activities": n_activities,
                "kind": "automation" if name in automation else "unclassified",
            } for name, events, n_cases, n_activities in resources],
            "resources_truncated": truncated,
            "handover_of_work": [{"from": a, "to": b, "handovers": n, "cases": c}
                                 for a, b, n, c in handovers],
            "cases_with_handover": handover_cases,
            "subcontracting": [{"from": a, "to": b, "times": n, "cases": c}
                               for a, b, n, c in subcontracting],
            "working_together": [{"resources": [a, b], "shared_cases": n}
                                 for a, b, n in together],
            "working_together_excluded_cases": crowded,
            "activity_resource_matrix": {
                "activities": top_activities,
                "resources": [label[name] for name in matrix_resources],
                "events": [[lookup.get((activity, name), 0) for name in matrix_resources] for activity in top_activities],
            },
            "coverage": {"events": total_events, "events_with_resource": with_resource, "cases": cases,
                         "resource_share": with_resource / total_events if total_events else None},
            "cohort": cohorts.describe(chosen),
            "names": names,
            "privacy": {
                "pseudonymous_by_default": True,
                "per_person_durations_or_speed_computed": False,
                "ranking_or_performance_scoring": False,
                "labels_are_stable_within_this_response_only": names == "pseudonymous",
            },
            "interpretation": ("Handover counts consecutive events in a case performed by different resources; "
                               "subcontracting counts work that passes from one resource to another and straight "
                               "back; working together counts cases two resources both touched. None says who "
                               "was responsible or how well anyone worked."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["organization", "relationship_cases"]
