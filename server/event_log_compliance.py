"""Declarative compliance rules over saved event logs (roadmap #326 G2).

Declare-style templates checked per case, as in ProM Declare, ABBYY Timeline
or Signavio process adherence:

- ``response``: every A is eventually followed by B;
- ``precedence``: every B is preceded by some A;
- ``response_within``: every A is followed by B within N seconds;
- ``existence``: A occurs between ``min`` and ``max`` times;
- ``not_coexistence``: A and B never both occur in a case;
- ``segregation_of_duties``: no single resource performs both A and B (four
  eyes); cases where either step has no recorded resource are reported as
  unknown, never as satisfied.

Event order is time, then event ID, the same tie-break as the rest of the
workspace. A case "activates" a rule when the rule's trigger occurs in it;
other cases satisfy it vacuously and are counted separately. Results describe
the imported log only: they are not verified policy, audit evidence or legal
compliance, and no resource names are returned.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_analytics import _wilson

MAX_RULES = 20
MAX_EVIDENCE_CASES = 50
Label = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pair(_Strict):
    template: Literal["response", "precedence", "not_coexistence", "segregation_of_duties"]
    a: Label
    b: Label


class Within(_Strict):
    template: Literal["response_within"]
    a: Label
    b: Label
    seconds: StrictInt = Field(ge=1, le=10 ** 9)


class Existence(_Strict):
    template: Literal["existence"]
    a: Label
    min: StrictInt = Field(default=0, ge=0, le=10 ** 6)
    max: StrictInt | None = Field(default=None, ge=0, le=10 ** 6)


Rule = Annotated[Union[Pair, Within, Existence], Field(discriminator="template")]


class RuleSet(_Strict):
    rules: list[Rule] = Field(min_length=1, max_length=MAX_RULES)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: Label | None = None


def parse(spec: Any) -> RuleSet:
    try:
        parsed = RuleSet.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "compliance rules are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    for rule in parsed.rules:
        if isinstance(rule, Existence):
            if rule.max is not None and rule.max < rule.min:
                raise workspace.EventLogWorkspaceError("existence max must not be below min")
            if rule.min == 0 and rule.max is None:
                raise workspace.EventLogWorkspaceError("existence needs a min above 0 or a max")
        elif rule.a == rule.b:
            raise workspace.EventLogWorkspaceError(f"{rule.template} needs two different activities")
    return parsed


# Per-case outcome for each template: activated, violated, unknown. Positions
# follow (ts, event_id) so ties are ordered the same way everywhere.
def positions(conn, table: str = "ce") -> None:
    """Materialize ordered case positions from ``ce`` or a cohort table into ``cp``."""
    if table not in {"ce", "rc"}:
        raise ValueError("unknown source table")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE cp AS SELECT case_id, activity, resource, ts,
        row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS pos FROM {table}""")


def _outcomes(conn, rule) -> tuple[str, list[Any]]:
    """SQL returning (case_id, activated, violated, unknown, detail) for every case."""
    if isinstance(rule, Existence):
        return ("""SELECT case_id, TRUE, n < ? OR n > coalesce(?, 9223372036854775807), FALSE,
                     CAST(n AS VARCHAR) || ' occurrences'
                   FROM (SELECT case_id, count(*) FILTER (WHERE activity = ?) AS n FROM cp GROUP BY case_id)""",
                [rule.min, rule.max, rule.a])
    if rule.template == "response":
        return ("""SELECT case_id, bool_or(activity = ?),
                     max(pos) FILTER (WHERE activity = ?) > coalesce(max(pos) FILTER (WHERE activity = ?), 0), FALSE,
                     CASE WHEN max(pos) FILTER (WHERE activity = ?) IS NULL THEN 'no later step' ELSE 'last step not followed' END
                   FROM cp GROUP BY case_id""", [rule.a, rule.a, rule.b, rule.b])
    if rule.template == "precedence":
        return ("""SELECT case_id, bool_or(activity = ?),
                     coalesce(min(pos) FILTER (WHERE activity = ?), 1e18) > min(pos) FILTER (WHERE activity = ?), FALSE,
                     CASE WHEN min(pos) FILTER (WHERE activity = ?) IS NULL THEN 'required earlier step missing'
                          ELSE 'required earlier step came later' END
                   FROM cp GROUP BY case_id""", [rule.b, rule.a, rule.b, rule.a])
    if rule.template == "not_coexistence":
        return ("""SELECT case_id, bool_or(activity = ?) OR bool_or(activity = ?),
                     bool_or(activity = ?) AND bool_or(activity = ?), FALSE, 'both steps occur'
                   FROM cp GROUP BY case_id""", [rule.a, rule.b, rule.a, rule.b])
    if rule.template == "segregation_of_duties":
        return ("""WITH per AS (SELECT case_id,
                       list(DISTINCT resource) FILTER (WHERE activity = ? AND resource <> '') AS ra,
                       list(DISTINCT resource) FILTER (WHERE activity = ? AND resource <> '') AS rb,
                       bool_or(activity = ?) AS has_a, bool_or(activity = ?) AS has_b,
                       bool_or(activity = ? AND resource = '') OR bool_or(activity = ? AND resource = '') AS missing
                     FROM cp GROUP BY case_id)
                   SELECT case_id, has_a AND has_b,
                     has_a AND has_b AND len(list_intersect(coalesce(ra, []), coalesce(rb, []))) > 0,
                     has_a AND has_b AND missing AND len(list_intersect(coalesce(ra, []), coalesce(rb, []))) = 0,
                     'same resource performed both steps' FROM per""",
                [rule.a, rule.b, rule.a, rule.b, rule.a, rule.b])
    # response_within: each A needs a B later in the case within the limit.
    return ("""WITH a AS (SELECT case_id, pos, ts FROM cp WHERE activity = ?),
                    b AS (SELECT case_id, pos, ts FROM cp WHERE activity = ?),
                    nxt AS (SELECT a.case_id, a.ts AS a_ts,
                              (SELECT min(b.ts) FROM b WHERE b.case_id = a.case_id AND b.pos > a.pos) AS b_ts FROM a),
                    per AS (SELECT case_id, bool_or(b_ts IS NULL OR epoch(b_ts - a_ts) > ?) AS late,
                              max(epoch(b_ts - a_ts)) AS worst FROM nxt GROUP BY case_id)
               SELECT c.case_id, per.case_id IS NOT NULL, coalesce(per.late, FALSE), FALSE,
                      CASE WHEN per.worst IS NULL THEN 'no later step' ELSE 'slowest gap ' || CAST(round(per.worst) AS VARCHAR) || ' s' END
               FROM (SELECT DISTINCT case_id FROM cp) c LEFT JOIN per ON per.case_id = c.case_id""",
            [rule.a, rule.b, rule.seconds])


def _describe(rule) -> str:
    if isinstance(rule, Existence):
        bounds = f"at least {rule.min}" if rule.max is None else (
            f"exactly {rule.min}" if rule.min == rule.max else f"between {rule.min} and {rule.max}")
        return f"{rule.a} occurs {bounds} times"
    return {
        "response": f"every {rule.a} is eventually followed by {rule.b}",
        "precedence": f"every {rule.b} is preceded by {rule.a}",
        "not_coexistence": f"{rule.a} and {rule.b} never occur in the same case",
        "segregation_of_duties": f"no single resource performs both {rule.a} and {rule.b}",
        "response_within": f"every {rule.a} is followed by {rule.b} within {getattr(rule, 'seconds', 0)} s",
    }[rule.template]


def evaluate_rule(conn, rule) -> tuple[dict[str, Any], list[str]]:
    """Evaluate one rule over the ``cp`` table; returns the summary and all violating case IDs."""
    sql, params = _outcomes(conn, rule)
    conn.execute(f"CREATE OR REPLACE TEMP TABLE co AS SELECT * FROM ({sql}) t(case_id, activated, violated, unknown, detail)",
                 params)
    cases, activated, violated, unknown = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE activated), count(*) FILTER (WHERE violated), "
        "count(*) FILTER (WHERE unknown) FROM co").fetchone()
    evidence = [{"case_id": c, "detail": d} for c, d in conn.execute(
        "SELECT case_id, detail FROM co WHERE violated ORDER BY case_id LIMIT ?", [MAX_EVIDENCE_CASES]).fetchall()]
    ids = [row[0] for row in conn.execute("SELECT case_id FROM co WHERE violated ORDER BY case_id").fetchall()]
    decided = activated - unknown
    summary: dict[str, Any] = {
        "rule": rule.model_dump(), "description": _describe(rule), "cases": cases,
        "activated_cases": activated, "vacuously_satisfied_cases": cases - activated,
        "violating_cases": violated, "satisfied_cases": decided - violated, "unknown_cases": unknown,
        "violation_rate": violated / decided if decided else None,
        "violation_rate_ci95": list(_wilson(violated, decided, 1.959964)) if decided else None,
        "violating_case_examples": evidence, "examples_truncated": violated > len(evidence),
    }
    return summary, ids


def check(log_id: str, spec: Any) -> dict[str, Any]:
    rules = parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(rules.cohort), log_id)
        positions(conn)
        results = [evaluate_rule(conn, rule)[0] for rule in rules.rules]
        return {
            "results": results,
            "rules_with_violations": sum(1 for r in results if r["violating_cases"]),
            "cohort": cohorts.describe(cohorts.parse_cohort(rules.cohort)),
            "ordering_basis": "timestamp_then_event_id",
            "resource_names_returned": False,
            "interpretation": ("Checks describe the imported log against rules you entered. They are not verified "
                               "policy, audit evidence or legal compliance; a violation may reflect missing events."),
        }
    return workspace._query(log_id, rules.case_type, run)


__all__ = ["Rule", "check", "evaluate_rule", "parse", "positions"]
