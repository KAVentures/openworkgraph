"""Discover candidate Declare-style rules from a saved event log.

ProM's Declare Miner and Signavio's rule suggestions propose constraints that most
cases already follow. This does the same for the templates the compliance checker
(``event_log_compliance``) can evaluate:

* ``existence``: an activity occurs in (nearly) every case, or at most once when it occurs;
* ``response``: every A is eventually followed by B;
* ``precedence``: every B is preceded by some A;
* ``not_coexistence``: A and B (nearly) never occur in the same case.

Each candidate reports its *support* (the share of cases that activate it) and its
*confidence* (the share of activated cases that satisfy it, with a Wilson 95%
interval), plus how many cases violate it. Candidates come back as a rule set in the
compliance checker's own format, so a reviewer can check, edit and save them.

They are observed regularities of the imported log, not policy: a rule that 98% of
cases follow may be a habit, a system constraint or a gap in the data.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_analytics import _wilson
from .event_log_compliance import MAX_RULES, positions

MAX_ACTIVITIES = 40
MAX_CANDIDATES = 100
TEMPLATES = ("existence", "response", "precedence", "not_coexistence")


class DiscoverySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    min_support: float = Field(default=0.1, ge=0.0, le=1.0)
    min_confidence: float = Field(default=0.95, ge=0.5, le=1.0)
    max_activities: int = Field(default=25, ge=2, le=MAX_ACTIVITIES)
    max_candidates: int = Field(default=50, ge=1, le=MAX_CANDIDATES)
    templates: list[str] = Field(default_factory=lambda: list(TEMPLATES), min_length=1, max_length=len(TEMPLATES))
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: str | None = Field(default=None, min_length=1, max_length=200)


def _parse(spec: Any) -> DiscoverySpec:
    try:
        parsed = DiscoverySpec.model_validate(spec or {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "rule discovery settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    if set(parsed.templates) - set(TEMPLATES) or len(set(parsed.templates)) != len(parsed.templates):
        raise workspace.EventLogWorkspaceError("templates are " + ", ".join(TEMPLATES))
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _candidate(rule: dict[str, Any], description: str, activated: int, satisfied: int, cases: int) -> dict[str, Any]:
    low, high = _wilson(satisfied, activated, 1.959964)
    return {"rule": rule, "description": description, "activated_cases": activated,
            "satisfying_cases": satisfied, "violating_cases": activated - satisfied,
            "support": round(activated / cases, 6), "confidence": round(satisfied / activated, 6),
            "confidence_ci95": [round(low, 6), round(high, 6)]}


def discover(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        positions(conn)
        cases = conn.execute("SELECT count(DISTINCT case_id) FROM cp").fetchone()[0]
        if not cases:
            return {"cases": 0, "candidates": [], "rule_set": None}
        conn.execute("""CREATE OR REPLACE TEMP TABLE ap AS SELECT case_id, activity, count(*) AS n,
                          min(pos) AS first_pos, max(pos) AS last_pos FROM cp GROUP BY case_id, activity""")
        freq = conn.execute("SELECT activity, count(*) FROM ap GROUP BY activity ORDER BY count(*) DESC, activity "
                            "LIMIT ?", [settings.max_activities]).fetchall()
        considered = {a: n for a, n in freq}
        total_activities = conn.execute("SELECT count(DISTINCT activity) FROM ap").fetchone()[0]
        conn.execute("CREATE OR REPLACE TEMP TABLE top_a AS SELECT unnest(?) AS activity", [list(considered)])
        out: list[dict[str, Any]] = []
        want = set(settings.templates)
        floor = settings.min_support * cases
        if "existence" in want:
            for activity, present in considered.items():
                if present / cases >= settings.min_confidence:
                    out.append(_candidate({"template": "existence", "a": activity, "min": 1}, f"{activity} occurs in every case",
                                          cases, present, cases))
            once = dict(conn.execute("SELECT activity, count(*) FILTER (WHERE n = 1) FROM ap WHERE activity IN "
                                     "(SELECT activity FROM top_a) GROUP BY activity").fetchall())
            for activity, present in considered.items():
                # Like the checker, every case activates an existence rule; only repeats violate "at most once".
                repeats = present - once.get(activity, 0)
                if present >= max(floor, 1) and (cases - repeats) / cases >= settings.min_confidence:
                    out.append(_candidate({"template": "existence", "a": activity, "min": 0, "max": 1},
                                          f"{activity} occurs at most once", cases, cases - repeats, cases))
        if want & {"response", "precedence", "not_coexistence"}:
            pairs = conn.execute("""SELECT a.activity, b.activity, count(*) AS both_cases,
                    count(*) FILTER (WHERE b.last_pos > a.last_pos) AS a_then_b_last,
                    count(*) FILTER (WHERE a.first_pos < b.first_pos) AS a_before_first_b
                FROM ap a JOIN ap b ON a.case_id = b.case_id AND a.activity <> b.activity
                WHERE a.activity IN (SELECT activity FROM top_a) AND b.activity IN (SELECT activity FROM top_a)
                GROUP BY a.activity, b.activity""").fetchall()
            seen = {(a, b): (both, resp, prec) for a, b, both, resp, prec in pairs}
            for a, fa in considered.items():
                for b, fb in considered.items():
                    if a == b:
                        continue
                    both, resp, prec = seen.get((a, b), (0, 0, 0))
                    # Response: activated by every case containing A; satisfied when some B comes after the last A.
                    if "response" in want and fa >= floor and fa and resp / fa >= settings.min_confidence:
                        out.append(_candidate({"template": "response", "a": a, "b": b},
                                              f"every {a} is eventually followed by {b}", fa, resp, cases))
                    # Precedence: activated by every case containing B; satisfied when an A comes before the first B.
                    if "precedence" in want and fb >= floor and fb and prec / fb >= settings.min_confidence:
                        out.append(_candidate({"template": "precedence", "a": a, "b": b},
                                              f"every {b} is preceded by {a}", fb, prec, cases))
                    if "not_coexistence" in want and a < b and fa >= floor and fb >= floor:
                        either = fa + fb - both
                        if either and (either - both) / either >= settings.min_confidence:
                            out.append(_candidate({"template": "not_coexistence", "a": a, "b": b},
                                                  f"{a} and {b} never occur in the same case", either, either - both, cases))
        # Most certain and most widely applicable first; ties by description for stable output.
        out.sort(key=lambda c: (-c["confidence_ci95"][0], -c["support"], c["description"]))
        found = len(out)
        out = out[:settings.max_candidates]
        rule_set = {"rules": [c["rule"] for c in out[:MAX_RULES]], "cohort": settings.cohort,
                    **({"case_type": settings.case_type} if settings.case_type else {})} if out else None
        return {"cases": cases, "activities_considered": len(considered), "activities_in_log": total_activities,
                "activities_truncated": total_activities > len(considered), "candidates": out,
                "candidates_found": found, "candidates_truncated": found > len(out), "rule_set": rule_set,
                "rule_set_limit": MAX_RULES, "settings": settings.model_dump(exclude={"cohort", "case_type"}),
                "cohort": cohorts.describe(cohorts.parse_cohort(settings.cohort)),
                "ordering_basis": "timestamp_then_event_id",
                "interpretation": ("Candidates are regularities most cases of this imported log already follow. They are "
                                   "not policy, controls or verified business rules; review each before using it as a check.")}
    return workspace._query(log_id, settings.case_type, run)


__all__ = ["TEMPLATES", "discover"]
