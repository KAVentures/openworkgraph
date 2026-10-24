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
from . import event_log_workspace as workspace

MAX_RESOURCES = 200
MAX_EDGES = 500
MAX_MATRIX_ACTIVITIES = 30
MAX_MATRIX_RESOURCES = 30
# Cases touched by more resources than this are excluded from the
# working-together pairs (quadratic) and reported, never silently clipped.
MAX_RESOURCES_PER_CASE = 50
_NAMES = {"pseudonymous", "shown"}


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

        def show(name: str) -> str:
            return label.get(name, "Other resources")

        handovers = conn.execute("""SELECT resource, nxt, count(*) AS n, count(DISTINCT case_id) AS cases FROM (
              SELECT case_id, resource, lead(resource) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS nxt FROM ce)
            WHERE resource <> '' AND nxt <> '' AND nxt IS NOT NULL AND resource <> nxt
            GROUP BY resource, nxt ORDER BY n DESC, resource, nxt""").fetchall()
        crowded = conn.execute("""SELECT count(*) FROM (SELECT case_id FROM ce WHERE resource <> ''
            GROUP BY case_id HAVING count(DISTINCT resource) > ?)""", [MAX_RESOURCES_PER_CASE]).fetchone()[0]
        together = conn.execute("""WITH pr AS (
              SELECT DISTINCT case_id, resource FROM ce WHERE resource <> '' AND case_id IN (
                SELECT case_id FROM ce WHERE resource <> '' GROUP BY case_id HAVING count(DISTINCT resource) <= ?))
            SELECT a.resource, b.resource, count(*) AS cases FROM pr a JOIN pr b
              ON a.case_id = b.case_id AND a.resource < b.resource
            GROUP BY a.resource, b.resource ORDER BY cases DESC, a.resource, b.resource LIMIT ?""",
            [MAX_RESOURCES_PER_CASE, MAX_EDGES * 4]).fetchall()
        top_activities = [row[0] for row in conn.execute("""SELECT activity FROM ce WHERE resource <> ''
            GROUP BY activity ORDER BY count(*) DESC, activity LIMIT ?""", [MAX_MATRIX_ACTIVITIES]).fetchall()]
        matrix_resources = [row[0] for row in resources[:MAX_MATRIX_RESOURCES]]
        cells = conn.execute("""SELECT activity, resource, count(*) FROM ce WHERE resource <> ''
            GROUP BY activity, resource""").fetchall()
        handover_cases = conn.execute("""SELECT count(DISTINCT case_id) FROM (
              SELECT case_id, resource, lead(resource) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS nxt FROM ce)
            WHERE resource <> '' AND nxt <> '' AND nxt IS NOT NULL AND resource <> nxt""").fetchone()[0]

        def merge(rows, width):
            # Resources beyond the visible set are folded into "Other resources"
            # so edge counts stay complete without naming them.
            merged: dict[tuple, list[int]] = {}
            for row in rows:
                key = tuple(show(value) for value in row[:2])
                if key[0] == key[1]:
                    continue
                slot = merged.setdefault(key, [0] * width)
                for i in range(width):
                    slot[i] += row[2 + i]
            return sorted(merged.items(), key=lambda item: (-item[1][0], item[0]))

        lookup = {(a, r): n for a, r, n in cells}
        return {
            "resources": [{
                "resource": show(name), "events": events, "cases": n_cases, "activities": n_activities,
                "kind": "automation" if name in automation else "unclassified",
            } for name, events, n_cases, n_activities in resources],
            "resources_truncated": truncated,
            "handover_of_work": [{"from": a, "to": b, "handovers": n, "cases": c}
                                 for (a, b), (n, c) in merge(handovers, 2)[:limit]],
            "cases_with_handover": handover_cases,
            "working_together": [{"resources": [a, b], "shared_cases": n}
                                 for (a, b), (n,) in merge(together, 1)[:limit]],
            "working_together_excluded_cases": crowded,
            "activity_resource_matrix": {
                "activities": top_activities,
                "resources": [show(name) for name in matrix_resources],
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
                               "working together counts cases two resources both touched. Neither says who "
                               "was responsible or how well anyone worked."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["organization"]
