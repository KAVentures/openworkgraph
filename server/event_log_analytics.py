"""Conformance, KPI and root-cause analytics over the local event-log workspace.

Everything here describes an imported log. Conformance compares observed
variants with a reference model the user supplies; root-cause results are
statistical associations between case features and an outcome, with
confidence intervals, never causal findings.
"""
from __future__ import annotations

from math import sqrt
from statistics import NormalDist
from typing import Any

from . import event_log_workspace as workspace
from .event_log_workspace import EventLogWorkspaceError

_SEP = workspace._SEP
MAX_MODEL_PATHS = 10
MAX_MODEL_PATH_LENGTH = 100
MAX_ALIGNED_VARIANTS = 2000
MAX_ALIGNED_TRACE_LENGTH = 300
MAX_AUTOMATION_RESOURCES = 50
MIN_GROUP_CASES = 5
_OUTCOMES = {"duration_over", "contains_activity", "ends_with", "rework"}


def _text_list(values: Any, limit: int, what: str) -> list[str]:
    if not isinstance(values, list) or not values or len(values) > limit:
        raise EventLogWorkspaceError(f"{what} must be a non-empty list of at most {limit} items")
    if not all(isinstance(v, str) and 0 < len(v) <= 200 for v in values):
        raise EventLogWorkspaceError(f"{what} must contain short activity names")
    return values


def parse_reference_model(model: Any) -> list[list[str]]:
    """A reviewed process as one or more accepted activity paths."""
    if not isinstance(model, dict) or model.get("format") != "openworkgraph.reference-model.v1":
        raise EventLogWorkspaceError("reference model must use format openworkgraph.reference-model.v1")
    paths = model.get("paths")
    if not isinstance(paths, list) or not 0 < len(paths) <= MAX_MODEL_PATHS:
        raise EventLogWorkspaceError(f"reference model needs 1 to {MAX_MODEL_PATHS} paths")
    return [_text_list(path, MAX_MODEL_PATH_LENGTH, "each model path") for path in paths]


# ---------------------------------------------------------------- conformance

def _align(trace: list[str], path: list[str]) -> tuple[int, list[tuple[str, str]]]:
    """Optimal alignment with unit-cost log and model moves (no substitutions)."""
    n, m = len(trace), len(path)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        cost[i][0] = i
    for j in range(1, m + 1):
        cost[0][j] = j
    for i in range(1, n + 1):
        row, prev, activity = cost[i], cost[i - 1], trace[i - 1]
        for j in range(1, m + 1):
            best = min(prev[j], row[j - 1]) + 1
            if activity == path[j - 1] and prev[j - 1] < best:
                best = prev[j - 1]
            row[j] = best
    moves: list[tuple[str, str]] = []
    i, j = n, m
    while i or j:
        if i and j and trace[i - 1] == path[j - 1] and cost[i][j] == cost[i - 1][j - 1]:
            moves.append(("sync", trace[i - 1])); i -= 1; j -= 1
        elif i and cost[i][j] == cost[i - 1][j] + 1:
            moves.append(("log", trace[i - 1])); i -= 1
        else:
            moves.append(("model", path[j - 1])); j -= 1
    moves.reverse()
    return cost[n][m], moves


def conformance(log_id: str, model: Any, case_type: str | None = None,
                max_variants: int = 500) -> dict[str, Any]:
    paths = parse_reference_model(model)
    max_variants = max(1, min(int(max_variants), MAX_ALIGNED_VARIANTS))
    shortest = min(len(p) for p in paths)

    def run(conn):
        total_cases = conn.execute("SELECT count(DISTINCT case_id) FROM ce").fetchone()[0]
        rows = conn.execute(f"""SELECT path, count(*) AS cases, count(*) OVER () AS observed_variants FROM (
              SELECT string_agg(activity, '{_SEP}' ORDER BY ts, event_id) AS path FROM ce GROUP BY case_id)
            GROUP BY path ORDER BY cases DESC, path LIMIT {max_variants}""").fetchall()
        observed_variants = rows[0][2] if rows else 0
        aligned_cases = fitting_cases = 0
        fitness_sum = 0.0
        skipped: dict[str, int] = {}
        inserted: dict[str, int] = {}
        too_long = 0
        variants_aligned = variants_skipped_too_long = 0
        worst: list[dict[str, Any]] = []
        for joined, cases, _ in rows:
            trace = joined.split(_SEP)
            if len(trace) > MAX_ALIGNED_TRACE_LENGTH:
                too_long += cases
                variants_skipped_too_long += 1
                continue
            variants_aligned += 1
            best_cost, best_moves, best_path = None, [], 0
            for index, path in enumerate(paths):
                cost, moves = _align(trace, path)
                if best_cost is None or cost < best_cost:
                    best_cost, best_moves, best_path = cost, moves, index
            fitness = 1 - best_cost / (len(trace) + shortest)
            aligned_cases += cases
            fitness_sum += fitness * cases
            fitting_cases += cases if best_cost == 0 else 0
            for kind, activity in best_moves:
                bucket = skipped if kind == "model" else inserted if kind == "log" else None
                if bucket is not None:
                    bucket[activity] = bucket.get(activity, 0) + cases
            if best_cost:
                worst.append({"path": trace, "cases": cases, "fitness": round(fitness, 6),
                              "closest_model_path": best_path,
                              "deviations": [{"move": "skipped" if k == "model" else "extra", "activity": a}
                                             for k, a in best_moves if k != "sync"][:50]})
        worst.sort(key=lambda v: (-v["cases"], v["fitness"]))

        def ranked(counts):
            return [{"activity": a, "cases": n, "share_of_aligned_cases": round(n / aligned_cases, 6)}
                    for a, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]]
        return {
            "model_paths": len(paths),
            "max_variants_applied": max_variants,
            "variants_selected": len(rows),
            "variants_observed": observed_variants,
            "variants_not_selected_variant_limit": observed_variants - len(rows),
            "variants_aligned": variants_aligned,
            "variants_skipped_trace_too_long": variants_skipped_too_long,
            "cases": total_cases,
            "aligned_cases": aligned_cases,
            "aligned_share": round(aligned_cases / total_cases, 6) if total_cases else 0,
            "cases_not_aligned_trace_too_long": too_long,
            # Distinguish ranked-variant cap omissions from selected traces too long to align.
            # These two counts plus aligned_cases partition all selected log cases.
            "cases_not_aligned_variant_limit": total_cases - aligned_cases - too_long,
            "variant_limit_truncated": total_cases > aligned_cases + too_long,
            "fitness": round(fitness_sum / aligned_cases, 6) if aligned_cases else None,
            "fitting_cases": fitting_cases,
            "fitting_share": round(fitting_cases / aligned_cases, 6) if aligned_cases else None,
            "skipped_model_activities": ranked(skipped),
            "extra_observed_activities": ranked(inserted),
            "deviating_variants": worst[:25],
            "method": "optimal alignment, unit cost per log or model move, best of the reference paths",
            "reference_model_is_user_supplied": True,
        }
    return workspace._query(log_id, case_type, run)


# ---------------------------------------------------------------- KPIs

def kpis(log_id: str, case_type: str | None = None, sla_seconds: int | None = None,
         automation_resources: list[str] | None = None, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    from .event_log_cohorts import describe, parse_cohort, restrict
    chosen = parse_cohort(cohort)
    automation = list(dict.fromkeys(r for r in (automation_resources or []) if r))
    if len(automation) > MAX_AUTOMATION_RESOURCES or any(len(r) > 200 for r in automation):
        raise EventLogWorkspaceError("too many or too long automation resource names")
    if sla_seconds is not None and not 0 < int(sla_seconds) <= 10 ** 9:
        raise EventLogWorkspaceError("sla_seconds must be a positive number of seconds")

    def run(conn):
        restrict(conn, chosen, log_id)
        conn.execute("""CREATE OR REPLACE TEMP TABLE cs AS SELECT case_id, count(*) AS events,
              epoch(max(ts) - min(ts)) AS duration, max(ts) AS ended,
              count(*) > count(DISTINCT activity) AS rework
            FROM ce GROUP BY case_id""")
        cases, q, mean, events, rework = conn.execute("""SELECT count(*), quantile_cont(duration, [0.5, 0.9, 0.95]),
              avg(duration), avg(events), count(*) FILTER (WHERE rework) FROM cs""").fetchone()
        result: dict[str, Any] = {
            "cohort": describe(chosen),
            "cases": cases,
            "throughput_seconds": {"p50": q[0], "p90": q[1], "p95": q[2], "mean": mean} if cases else None,
            "events_per_case": round(events, 3) if cases else None,
            "rework_cases": rework,
            "rework_rate": round(rework / cases, 6) if cases else None,
            "rework_definition": "a case repeats at least one activity",
        }
        if sla_seconds is not None:
            breaches = conn.execute("SELECT count(*) FROM cs WHERE duration > ?", [int(sla_seconds)]).fetchone()[0]
            result["sla"] = {"seconds": int(sla_seconds), "breached_cases": breaches,
                             "breach_rate": round(breaches / cases, 6) if cases else None}
        if automation:
            total, automated = conn.execute("""SELECT count(*), count(*) FILTER (WHERE list_contains(?, resource))
                FROM ce""", [automation]).fetchone()
            result["automation"] = {"resources": automation, "events": total, "automated_events": automated,
                                    "automation_rate": round(automated / total, 6) if total else None,
                                    "resources_declared_by_user": True}
        else:
            result["automation"] = None
        weekly = conn.execute("""SELECT strftime(date_trunc('week', ended), '%Y-%m-%d') AS week, count(*),
              quantile_cont(duration, 0.5) FROM cs GROUP BY 1 ORDER BY 1 DESC LIMIT 52""").fetchall()
        result["completed_per_week"] = [{"week_starting": w, "cases": n, "median_throughput_seconds": d}
                                        for w, n, d in reversed(weekly)]
        return result
    return workspace._query(log_id, case_type, run)


# ---------------------------------------------------------------- root cause

def _wilson(successes: int, total: int, z: float) -> tuple[float, float]:
    p = successes / total
    centre = (p + z * z / (2 * total)) / (1 + z * z / total)
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return centre - half, centre + half


def risk_difference(y1: int, n1: int, y0: int, n0: int, z: float = 1.959964) -> tuple[float, float, float]:
    """Difference of proportions with the Newcombe hybrid-score interval."""
    p1, p0 = y1 / n1, y0 / n0
    l1, u1 = _wilson(y1, n1, z)
    l0, u0 = _wilson(y0, n0, z)
    d = p1 - p0
    return d, d - sqrt((p1 - l1) ** 2 + (u0 - p0) ** 2), d + sqrt((u1 - p1) ** 2 + (p0 - l0) ** 2)


def root_cause(log_id: str, outcome: str, case_type: str | None = None, *, threshold_seconds: int | None = None,
               activity: str | None = None, limit: int = 30) -> dict[str, Any]:
    if outcome not in _OUTCOMES:
        raise EventLogWorkspaceError("outcome must be one of " + ", ".join(sorted(_OUTCOMES)))
    if outcome in {"duration_over"} and not (threshold_seconds and 0 < int(threshold_seconds) <= 10 ** 9):
        raise EventLogWorkspaceError("threshold_seconds is required for a duration outcome")
    if outcome in {"contains_activity", "ends_with"} and not (isinstance(activity, str) and 0 < len(activity) <= 200):
        raise EventLogWorkspaceError("activity is required for this outcome")
    limit = max(1, min(int(limit), 100))
    outcome_sql = {
        "duration_over": "epoch(max(ts) - min(ts)) > $threshold",
        "contains_activity": "bool_or(activity = $activity)",
        "ends_with": "arg_max(activity, (ts, event_id)) = $activity",
        "rework": "count(*) > count(DISTINCT activity)",
    }[outcome]
    params = {key: value for key, value in (("threshold", int(threshold_seconds or 0)), ("activity", activity or ""))
              if "$" + key in outcome_sql}

    def run(conn):
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE co AS
            SELECT case_id, {outcome_sql} AS y FROM ce GROUP BY case_id""", params)
        n, y = conn.execute("SELECT count(*), count(*) FILTER (WHERE y) FROM co").fetchone()
        base = {"cases": n, "outcome_cases": y, "outcome_rate": round(y / n, 6) if n else None,
                "outcome": {"kind": outcome, "threshold_seconds": threshold_seconds, "activity": activity}}
        if not n or y in (0, n):
            return {**base, "features_tested": 0, "associations": [],
                    "note": "the outcome does not vary across cases, so nothing can be associated with it"}
        excluded = activity if outcome in {"contains_activity", "ends_with"} else None
        rows = conn.execute("""WITH f AS (
              SELECT DISTINCT case_id, 'activity' AS kind, activity AS value FROM ce WHERE activity IS DISTINCT FROM $excluded
              UNION SELECT DISTINCT case_id, 'resource', resource FROM ce WHERE resource <> ''
              UNION SELECT DISTINCT o.object_id, 'attribute', left(kv.key, 100) || '=' || left(kv.value, 100)
                FROM objects o JOIN co ON co.case_id = o.object_id,
                  unnest(map_entries(json_transform(o.attributes, '"MAP(VARCHAR, VARCHAR)"'))) AS m(kv)
                WHERE o.log_id = $log_id)
            SELECT kind, value, count(*) AS n1, count(*) FILTER (WHERE co.y) AS y1
            FROM f JOIN co USING (case_id) GROUP BY 1, 2
            HAVING count(*) >= $min AND count(*) <= $max""",
                            {"excluded": excluded, "log_id": log_id,
                             "min": MIN_GROUP_CASES, "max": n - MIN_GROUP_CASES}).fetchall()
        tested = len(rows)
        z_adjusted = NormalDist().inv_cdf(1 - 0.05 / (2 * max(tested, 1)))
        found = []
        for kind, value, n1, y1 in rows:
            n0, y0 = n - n1, y - y1
            d, low, high = risk_difference(y1, n1, y0, n0)
            _, adj_low, adj_high = risk_difference(y1, n1, y0, n0, z_adjusted)
            found.append({
                "feature": {"kind": kind, "value": value},
                "cases_with_feature": n1, "outcome_rate_with": round(y1 / n1, 6),
                "cases_without_feature": n0, "outcome_rate_without": round(y0 / n0, 6),
                "risk_difference": round(d, 6), "ci95": [round(low, 6), round(high, 6)],
                "holds_after_bonferroni": adj_low > 0 or adj_high < 0,
            })
        found.sort(key=lambda r: (not r["holds_after_bonferroni"], -abs(r["risk_difference"]),
                                  r["feature"]["kind"], r["feature"]["value"]))
        return {**base, "features_tested": tested, "associations": found[:limit],
                "method": "risk difference with Newcombe hybrid-score 95% interval; Bonferroni over features tested",
                "associations_are_not_causes": True,
                "temporal_order_not_checked": True}

    return workspace._query(log_id, case_type, run)
