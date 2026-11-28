"""Automatic insights: where to look first in a saved event log.

One call runs a fixed set of cheap checks over the log and returns the findings
ranked by the case time they involve, so a new analyst sees the likely
bottlenecks, loops and outliers before opening any individual analysis. Each
finding carries its numbers, the share of cases it touches, a caveat, and the
case filter (``cohort``, and ``follows`` rules when expressible) that reproduces
it in every other analysis.

Checks:

* ``slow_transition``: a step-to-step gap that accounts for a large share of all
  recorded case time;
* ``repeated_activity``: an activity that recurs within cases, with the median
  duration of cases that repeat it against those that do not;
* ``costly_detour``: an activity present in a minority of cases whose cases take
  much longer than the rest;
* ``long_tail``: the slowest tenth of cases and their share of total case time;
* ``variant_spread``: how many distinct paths cover most cases;
* ``slowing_down``: newer cases taking longer than older ones;
* ``sla_breaches`` (with ``sla_seconds``): cases over the given duration.

Everything is descriptive: an insight says where recorded time and repetition
concentrate, not why, and every number can be re-derived from the log.
"""
from __future__ import annotations

import json

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

FORMAT = "openworkgraph.event-log-insights.v1"
MIN_CASES = 5
MAX_SEGMENTS = 3
NUMERIC_DISTINCT = 10  # numeric case attributes with more distinct values are grouped by quartile


class InsightSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_type: str | None = Field(default=None, min_length=1, max_length=200)
    cohort: dict[str, Any] = Field(default_factory=dict)
    sla_seconds: StrictInt | None = Field(default=None, ge=1, le=10 ** 9)
    limit: StrictInt = Field(default=8, ge=1, le=30)


def _parse(spec: Any) -> InsightSpec:
    try:
        parsed = InsightSpec.model_validate(spec if spec is not None else {})
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "insight settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    return parsed


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    return ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2


def insights(log_id: str, spec: Any = None) -> dict[str, Any]:
    settings = _parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE ins_cases AS
            SELECT case_id, min(ts) AS started, epoch(max(ts) - min(ts)) AS duration, count(*) AS events,
                   list(activity ORDER BY ts, event_id) AS path
            FROM ce GROUP BY case_id""")
        cases, total_time = conn.execute("SELECT count(*), coalesce(sum(duration), 0) FROM ins_cases").fetchone()
        data: dict[str, Any] = {"cases": cases, "total_time": float(total_time or 0)}
        if cases < MIN_CASES:
            return data
        data["median"], data["p90"] = conn.execute(
            "SELECT quantile_cont(duration, 0.5), quantile_cont(duration, 0.9) FROM ins_cases").fetchone()
        data["transitions"] = conn.execute("""SELECT activity, nxt, count(*) AS n, count(DISTINCT case_id) AS c,
              sum(gap) AS total, quantile_cont(gap, 0.5) AS median FROM (
                SELECT case_id, activity, lead(activity) OVER w AS nxt, epoch((lead(ts) OVER w) - ts) AS gap
                FROM ce WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id))
            WHERE nxt IS NOT NULL GROUP BY 1, 2 ORDER BY total DESC LIMIT 5""").fetchall()
        data["repeats"] = conn.execute("""WITH r AS (
              SELECT case_id, activity, count(*) AS k FROM ce GROUP BY 1, 2)
            SELECT r.activity, count(DISTINCT r.case_id) FILTER (WHERE r.k > 1) AS rep_cases,
                   sum(r.k - 1) FILTER (WHERE r.k > 1) AS extra,
                   quantile_cont(c.duration, 0.5) FILTER (WHERE r.k > 1) AS rep_median,
                   quantile_cont(c.duration, 0.5) FILTER (WHERE r.k = 1) AS once_median
            FROM r JOIN ins_cases c USING (case_id) GROUP BY 1
            HAVING count(DISTINCT r.case_id) FILTER (WHERE r.k > 1) > 0 ORDER BY rep_cases DESC LIMIT 5""").fetchall()
        data["detours"] = conn.execute("""WITH a AS (SELECT DISTINCT case_id, activity FROM ce)
            SELECT a.activity, count(*) AS with_cases, quantile_cont(c.duration, 0.5) AS with_median,
                   (SELECT quantile_cont(duration, 0.5) FROM ins_cases x
                    WHERE x.case_id NOT IN (SELECT case_id FROM a a2 WHERE a2.activity = a.activity)) AS without_median
            FROM a JOIN ins_cases c USING (case_id) GROUP BY 1
            HAVING count(*) < ? ORDER BY count(*) DESC LIMIT 40""", [cases * 0.5]).fetchall()
        # Case attributes (from the import or a joined case table): values, and quartile ranges of numeric ones.
        data["segments"] = conn.execute(f"""WITH attrs AS (
              SELECT c.case_id, left(kv.key, 100) AS k, kv.value AS v, try_cast(kv.value AS DOUBLE) AS x
              FROM objects o JOIN ins_cases c ON c.case_id = o.object_id,
                unnest(map_entries(json_transform(o.attributes, '"MAP(VARCHAR, VARCHAR)"'))) AS m(kv)
              WHERE o.log_id = ?),
            numeric AS (SELECT k, quantile_cont(x, [0.25, 0.5, 0.75]) AS q FROM attrs GROUP BY k
              HAVING count(x) >= 0.9 * count(*) AND count(DISTINCT x) > {NUMERIC_DISTINCT}),
            groups AS (
              SELECT DISTINCT case_id, k || '=' || v AS label, NULL::VARCHAR AS bounds FROM attrs
                WHERE k NOT IN (SELECT k FROM numeric) AND length(v) < 100
              UNION ALL
              SELECT DISTINCT a.case_id,
                CASE WHEN a.x < n.q[1] THEN a.k || ' < ' || printf('%.6g', n.q[1])
                     WHEN a.x < n.q[2] THEN printf('%.6g', n.q[1]) || ' ≤ ' || a.k || ' < ' || printf('%.6g', n.q[2])
                     WHEN a.x < n.q[3] THEN printf('%.6g', n.q[2]) || ' ≤ ' || a.k || ' < ' || printf('%.6g', n.q[3])
                     ELSE a.k || ' ≥ ' || printf('%.6g', n.q[3]) END,
                CASE WHEN a.x < n.q[1] THEN to_json({{'name': a.k, 'max': n.q[1]}})
                     WHEN a.x < n.q[2] THEN to_json({{'name': a.k, 'min': n.q[1], 'max': n.q[2]}})
                     WHEN a.x < n.q[3] THEN to_json({{'name': a.k, 'min': n.q[2], 'max': n.q[3]}})
                     ELSE to_json({{'name': a.k, 'min': n.q[3]}}) END::VARCHAR
              FROM attrs a JOIN numeric n USING (k) WHERE a.x IS NOT NULL),
            sized AS (SELECT label, any_value(bounds) AS bounds, count(*) AS n FROM groups GROUP BY label
              HAVING count(*) >= 3 AND count(*) <= ? ORDER BY count(*) DESC, label LIMIT 60)
            SELECT s.label, s.bounds, s.n, quantile_cont(c.duration, 0.5) AS with_median,
                   (SELECT quantile_cont(duration, 0.5) FROM ins_cases x WHERE x.case_id NOT IN
                      (SELECT case_id FROM groups g WHERE g.label = s.label)) AS without_median
            FROM sized s JOIN groups g USING (label) JOIN ins_cases c ON c.case_id = g.case_id
            GROUP BY s.label, s.bounds, s.n""", [log_id, cases * 0.8]).fetchall()
        data["tail"] = conn.execute("""SELECT count(*), coalesce(sum(duration), 0) FROM ins_cases
            WHERE duration > (SELECT quantile_cont(duration, 0.9) FROM ins_cases)""").fetchone()
        data["variants"] = [n for (n,) in conn.execute(
            "SELECT count(*) FROM ins_cases GROUP BY path ORDER BY count(*) DESC").fetchall()]
        data["halves"] = conn.execute("""WITH o AS (SELECT duration, ntile(2) OVER (ORDER BY started, case_id) AS h FROM ins_cases)
            SELECT h, count(*), quantile_cont(duration, 0.5) FROM o GROUP BY h ORDER BY h""").fetchall()
        if settings.sla_seconds:
            data["sla"] = conn.execute("SELECT count(*), coalesce(sum(duration - ?), 0) FROM ins_cases WHERE duration > ?",
                                       [settings.sla_seconds, settings.sla_seconds]).fetchone()
        return data

    data = workspace._query(log_id, settings.case_type, run)
    cases, total = data["cases"], data["total_time"]
    base_cohort = cohorts.describe(cohorts.parse_cohort(settings.cohort))
    findings: list[dict[str, Any]] = []
    if cases >= MIN_CASES:
        findings = _findings(data, cases, total, settings)
    findings.sort(key=lambda f: (-f["impact_seconds"], -f["case_share"], f["kind"], f["title"]))
    for rank, finding in enumerate(findings, 1):
        finding["rank"] = rank
    return {
        "format": FORMAT,
        "case_notion": data["case_notion"],
        "cases": cases,
        "cohort": base_cohort,
        "median_case_seconds": data.get("median"),
        "p90_case_seconds": data.get("p90"),
        "findings": findings[:settings.limit],
        "findings_total": len(findings),
        "too_few_cases": cases < MIN_CASES,
        "minimum_cases": MIN_CASES,
        "interpretation": ("Automatic, descriptive pointers to where recorded case time and repetition concentrate. "
                           "They are ranked by the case time involved and are not causes, recommendations or "
                           "verified waiting. Open the matching analysis with the given filter before acting."),
    }


def _findings(data: dict[str, Any], cases: int, total: float, settings: InsightSpec) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = total or 1.0
    for a, b, n, c, gap_total, gap_median in data["transitions"][:3]:
        share = gap_total / total
        if share < 0.15 or c < 3:
            continue
        out.append({
            "kind": "slow_transition", "title": f"{a} → {b} takes {share:.0%} of all recorded case time",
            "cases": c, "case_share": round(c / cases, 6), "impact_seconds": round(gap_total, 3),
            "numbers": {"from": a, "to": b, "transitions": n, "median_gap_seconds": gap_median,
                        "share_of_case_time": round(share, 6)},
            "cohort": {}, "follows": [{"from": a, "to": b, "mode": "direct", "negate": False}],
            "analysis": "waiting-time",
            "caveat": "Elapsed time between recorded events, not measured work or verified waiting."})
    for activity, rep_cases, extra, rep_median, once_median in data["repeats"]:
        share = rep_cases / cases
        if share < 0.05 or rep_cases < 3:
            continue
        excess = None if rep_median is None or once_median is None else rep_median - once_median
        out.append({
            "kind": "repeated_activity",
            "title": f"{activity} repeats in {share:.0%} of cases"
                     + (f"; those cases take {excess / 3600:.1f} h longer (median)" if excess and excess > 0 else ""),
            "cases": rep_cases, "case_share": round(share, 6),
            "impact_seconds": round(max(excess or 0, 0) * rep_cases, 3),
            "numbers": {"activity": activity, "extra_executions": int(extra or 0),
                        "median_seconds_when_repeated": rep_median, "median_seconds_when_once": once_median},
            "cohort": {}, "follows": [{"from": activity, "to": activity, "mode": "eventual", "negate": False}],
            "analysis": "rework",
            "caveat": "A repeated activity is not verified rework; it may be a legitimate second pass."})
    for activity, with_cases, with_median, without_median in data["detours"]:
        if with_cases < 3 or with_median is None or without_median is None or without_median <= 0:
            continue
        excess = with_median - without_median
        if excess <= 0 or with_median < 1.5 * without_median:
            continue
        out.append({
            "kind": "costly_detour",
            "title": f"Cases with {activity} ({with_cases / cases:.0%}) take {with_median / without_median:.1f}× as long",
            "cases": with_cases, "case_share": round(with_cases / cases, 6),
            "impact_seconds": round(excess * with_cases, 3),
            "numbers": {"activity": activity, "median_seconds_with": with_median,
                        "median_seconds_without": without_median},
            "cohort": {"activity": activity}, "follows": [],
            "analysis": "benchmark",
            "caveat": "Cases with this activity may differ in other ways; this is an association, not a cause."})
    segments = []
    for label, bounds, with_cases, with_median, without_median in data.get("segments", []):
        if with_median is None or without_median is None or without_median <= 0:
            continue
        excess = with_median - without_median
        if excess <= 0 or with_median < 1.5 * without_median:
            continue
        cohort = {"attribute_range": json.loads(bounds)} if bounds else {"attribute": label}
        segments.append({
            "kind": "attribute_segment",
            "title": f"Cases with {label} ({with_cases / cases:.0%}) take {with_median / without_median:.1f}× as long",
            "cases": with_cases, "case_share": round(with_cases / cases, 6),
            "impact_seconds": round(excess * with_cases, 3),
            "numbers": {"segment": label, "median_seconds_with": with_median, "median_seconds_without": without_median},
            "cohort": cohort, "follows": [], "analysis": "root-cause",
            "caveat": ("Cases in this group may differ in other ways (and numeric ranges are this log's quartiles); "
                       "this is an association, not a cause.")})
    # Keep the three largest, so one attribute with many values cannot crowd out the other findings.
    out += sorted(segments, key=lambda f: -f["impact_seconds"])[:MAX_SEGMENTS]
    tail_cases, tail_time = data["tail"]
    if tail_cases and data.get("median"):
        share = tail_time / total
        out.append({
            "kind": "long_tail",
            "title": f"The slowest {tail_cases} cases hold {share:.0%} of all case time",
            "cases": tail_cases, "case_share": round(tail_cases / cases, 6),
            "impact_seconds": round(max(tail_time - tail_cases * data["median"], 0), 3),
            "numbers": {"p90_seconds": data["p90"], "median_seconds": data["median"],
                        "share_of_case_time": round(share, 6)},
            "cohort": {}, "follows": [], "analysis": "duration-distribution",
            "caveat": "Duration is first-to-last recorded event; open cases may still be running."})
    counts = data["variants"]
    if counts:
        covered, k = 0, 0
        for n in counts:
            covered += n
            k += 1
            if covered >= 0.8 * cases:
                break
        out.append({
            "kind": "variant_spread",
            "title": f"{len(counts)} distinct paths; {k} of them cover 80% of cases",
            "cases": cases, "case_share": 1.0, "impact_seconds": 0.0,
            "numbers": {"distinct_variants": len(counts), "variants_for_80_percent": k,
                        "top_variant_share": round(counts[0] / cases, 6)},
            "cohort": {}, "follows": [], "analysis": "variant-coverage",
            "caveat": "Many paths can be legitimate case types rather than inconsistency."})
    halves = data["halves"]
    if len(halves) == 2 and halves[0][2] and halves[1][2] and halves[1][2] > 1.25 * halves[0][2] and halves[1][1] >= 3:
        (_, _, older), (_, newer_n, newer) = halves
        out.append({
            "kind": "slowing_down",
            "title": f"Newer cases take {newer / older:.1f}× as long as older ones (median)",
            "cases": newer_n, "case_share": round(newer_n / cases, 6),
            "impact_seconds": round((newer - older) * newer_n, 3),
            "numbers": {"older_median_seconds": older, "newer_median_seconds": newer},
            "cohort": {}, "follows": [], "analysis": "drift",
            "caveat": "Newer cases may simply be less complete; check open cases and the drift analysis."})
    if settings.sla_seconds and data.get("sla"):
        over, excess_time = data["sla"]
        if over:
            out.append({
                "kind": "sla_breaches",
                "title": f"{over} cases ({over / cases:.0%}) exceed {settings.sla_seconds / 3600:g} h",
                "cases": over, "case_share": round(over / cases, 6), "impact_seconds": round(excess_time, 3),
                "numbers": {"sla_seconds": settings.sla_seconds},
                "cohort": {}, "follows": [], "analysis": "kpis",
                "caveat": "Measured first-to-last recorded event against the duration you gave."})
    return out
