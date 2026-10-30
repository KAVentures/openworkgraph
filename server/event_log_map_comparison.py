"""Process map comparison between two cohorts (roadmap #326 H3).

Like Apromore's log comparison, the directly-follows maps of two cohorts (two
periods, teams, case types…) are compared path by path:

- frequency: the share of each cohort's cases that contain the path at least
  once, with the difference and a 95% interval;
- performance: the median gap between the two events of the path in each
  cohort, with a bootstrap interval on the difference for the most frequent
  paths.

Both maps are returned as well, so they can be drawn side by side. Differences
describe the two cohorts; they are not explained by the cohort definition.
"""
from __future__ import annotations

from statistics import median
from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_analytics import risk_difference

MAX_PATHS = 200
MAX_MAP_ACTIVITIES = 64
TIMED_PATHS = 25          # bootstrap intervals for the most frequent paths only
GAP_SAMPLE = 1000
GAP_ROUNDS = 300


def _gaps(conn, table: str, source: str, target: str) -> list[float]:
    return [r[0] for r in conn.execute(
        f"""SELECT gap FROM {table}_dfg WHERE src = ? AND dst = ?
            USING SAMPLE reservoir({GAP_SAMPLE} ROWS) REPEATABLE (7)""", [source, target]).fetchall()]


def _median_difference(a: list[float], b: list[float]) -> tuple[float, float, float]:
    return cohorts.median_difference(a, b, rounds=GAP_ROUNDS)


def compare_maps(log_id: str, cohort_specs: Any, case_type: str | None = None, *, limit: int = 40) -> dict[str, Any]:
    if not isinstance(cohort_specs, list) or len(cohort_specs) != 2:
        raise workspace.EventLogWorkspaceError("map comparison needs exactly two cohorts")
    first, second = cohorts.parse_cohort(cohort_specs[0]), cohorts.parse_cohort(cohort_specs[1])
    limit = max(1, min(int(limit), MAX_PATHS))

    def run(conn):
        cohorts.restrict(conn, first, log_id, "ca")
        cohorts.restrict(conn, second, log_id, "cb")
        sizes = {}
        for t in ("ca", "cb"):
            sizes[t] = conn.execute(f"SELECT count(DISTINCT case_id) FROM {t}").fetchone()[0]
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE {t}_dfg AS SELECT case_id, src, activity AS dst,
                  epoch(ts - prev_ts) AS gap FROM (
                    SELECT case_id, activity, ts, lag(activity) OVER w AS src, lag(ts) OVER w AS prev_ts
                    FROM {t} WINDOW w AS (PARTITION BY case_id ORDER BY ts, event_id)) WHERE src IS NOT NULL""")
        if not sizes["ca"] or not sizes["cb"]:
            raise workspace.EventLogWorkspaceError("each cohort needs at least one case")
        overlap = conn.execute("SELECT count(*) FROM (SELECT DISTINCT case_id FROM ca) JOIN "
                               "(SELECT DISTINCT case_id FROM cb) USING (case_id)").fetchone()[0]
        rows = conn.execute("""WITH a AS (SELECT src, dst, count(*) AS n, count(DISTINCT case_id) AS cases,
                  quantile_cont(gap, 0.5) AS med FROM ca_dfg GROUP BY 1, 2),
              b AS (SELECT src, dst, count(*) AS n, count(DISTINCT case_id) AS cases,
                  quantile_cont(gap, 0.5) AS med FROM cb_dfg GROUP BY 1, 2)
            SELECT coalesce(a.src, b.src), coalesce(a.dst, b.dst), coalesce(a.n, 0), coalesce(b.n, 0),
              coalesce(a.cases, 0), coalesce(b.cases, 0), a.med, b.med
            FROM a FULL JOIN b ON a.src = b.src AND a.dst = b.dst""").fetchall()
        na, nb = sizes["ca"], sizes["cb"]
        paths = []
        for src, dst, ta, tb, ca, cb, ma, mb in rows:
            d, low, high = risk_difference(cb, nb, ca, na)
            paths.append({"from": src, "to": dst, "transitions_in_first": ta, "transitions_in_second": tb,
                          "case_share_in_first": round(ca / na, 6), "case_share_in_second": round(cb / nb, 6),
                          "case_share_difference": round(d, 6), "ci95": [round(low, 6), round(high, 6)],
                          "ci_excludes_zero": low > 0 or high < 0,
                          "only_in": "first" if not cb else ("second" if not ca else None),
                          "median_gap_seconds_first": ma, "median_gap_seconds_second": mb})
        # Time intervals for the paths both cohorts use most.
        timed = sorted((p for p in paths if p["transitions_in_first"] > 1 and p["transitions_in_second"] > 1),
                       key=lambda p: -(p["transitions_in_first"] + p["transitions_in_second"]))[:TIMED_PATHS]
        for p in timed:
            # The full-cohort medians above and the reservoir-sampled bootstrap
            # below can have different point estimates. Expose the actual
            # sampled denominators and medians so the distinction is explicit.
            first_gaps = _gaps(conn, "ca", p["from"], p["to"])
            second_gaps = _gaps(conn, "cb", p["from"], p["to"])
            d, low, high = _median_difference(first_gaps, second_gaps)
            p["median_gap_difference"] = {"seconds": d, "ci95": [low, high], "ci_excludes_zero": low > 0 or high < 0,
                                          "sampled_gaps_first": len(first_gaps),
                                          "sampled_gaps_second": len(second_gaps),
                                          "sampled_median_gap_seconds_first": median(first_gaps),
                                          "sampled_median_gap_seconds_second": median(second_gaps),
                                          "sampling_truncated_first": len(first_gaps) < p["transitions_in_first"],
                                          "sampling_truncated_second": len(second_gaps) < p["transitions_in_second"],
                                          "method": f"percentile bootstrap, {GAP_ROUNDS} rounds, up to "
                                                    f"{GAP_SAMPLE} sampled gaps per cohort"}
        paths.sort(key=lambda p: (not p["ci_excludes_zero"], -abs(p["case_share_difference"]), p["from"], p["to"]))

        def side(t, n):
            # Fetch one extra ranked activity to distinguish an exactly-full
            # display from an actually truncated one without scanning all names.
            ranked = conn.execute(f"""SELECT activity, count(*), count(DISTINCT case_id) FROM {t}
                GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {MAX_MAP_ACTIVITIES + 1}""").fetchall()
            activities_truncated = len(ranked) > MAX_MAP_ACTIVITIES
            acts = ranked[:MAX_MAP_ACTIVITIES]
            kept = {a for a, _, _ in acts}
            edges = conn.execute(f"""SELECT src, dst, count(*), count(DISTINCT case_id), quantile_cont(gap, 0.5)
                FROM {t}_dfg GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2""").fetchall()
            starts = conn.execute(f"""SELECT activity, count(*) FROM (SELECT activity, row_number() OVER (
                PARTITION BY case_id ORDER BY ts, event_id) AS k FROM {t}) WHERE k = 1 GROUP BY 1
                ORDER BY 2 DESC, 1""").fetchall()
            return {"cases": n,
                    "activities": [{"activity": a, "event_count": e, "case_count": c} for a, e, c in acts],
                    "transitions": [{"from": s, "to": d, "transition_count": x, "case_count": c,
                                     "median_elapsed_gap_seconds": m}
                                    for s, d, x, c, m in edges if s in kept and d in kept][:4096],
                    "start_activities": {a: n for a, n in starts if a in kept},
                    "activities_truncated": activities_truncated}
        return {
            "cohorts": [cohorts.describe(first), cohorts.describe(second)],
            "cases": [na, nb], "overlapping_cases": overlap,
            "paths": paths[:limit], "paths_total": len(paths),
            "paths_only_in_first": sum(p["only_in"] == "first" for p in paths),
            "paths_only_in_second": sum(p["only_in"] == "second" for p in paths),
            "maps": [side("ca", na), side("cb", nb)],
            "intervals_assume_independent_cohorts": True,
            "note": ("cohorts share cases, so intervals overstate certainty" if overlap else
                     "differences describe these cohorts; they are not explained or caused by the cohort definition"),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["compare_maps"]
