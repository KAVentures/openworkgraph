"""Concept drift: when did the way cases run change? (roadmap #326 H4)

As in Apromore's or ProM's drift detection, cases are grouped into windows by
when they started (week, month, or a fixed number of cases). Each window's
behaviour is its directly-follows profile: for every path A → B, how many of
the window's cases contain it. Adjacent windows are compared with the
Jensen–Shannon distance between their profiles (0 = identical, 1 = disjoint),
and a permutation test says how often a random split of the same cases would
be at least that far apart. A boundary is reported as a change point when its
p-value stays below 0.05 after a Bonferroni correction for the number of
boundaries tested and the distance is at least ``MIN_DISTANCE``. When the observed distance
exceeds every permutation, the p-value comes from a normal tail fitted to the
permutation distances, so many boundaries can still be tested. For each change point the paths whose case share moved most
are listed, together with each window's median case duration.

A change point says the recorded behaviour differs between two windows. It
does not say why, and a change in how events are recorded looks the same.
"""
from __future__ import annotations

import json
import math
from typing import Any

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace

WINDOWS = ("week", "month", "cases")
MAX_WINDOWS = 60
SAMPLE_PER_WINDOW = 600
PERMUTATIONS = 99
ALPHA = 0.05
MIN_DISTANCE = 0.05   # practical size: tiny but "significant" shifts in large logs are not change points
_JSD = """
  WITH tot AS (SELECT {key}, side, sum(n) AS total FROM {src} GROUP BY ALL),
  dist AS (SELECT s.{key}, s.edge, s.side, s.n / t.total AS p FROM {src} s JOIN tot t USING ({key}, side)),
  wide AS (SELECT {key}, edge, coalesce(sum(p) FILTER (WHERE side = 0), 0) AS p,
             coalesce(sum(p) FILTER (WHERE side = 1), 0) AS q FROM dist GROUP BY ALL)
  SELECT {key}, sqrt(greatest(0, 0.5 * sum(CASE WHEN p > 0 THEN p * log2(p / ((p + q) / 2)) ELSE 0 END)
                   + 0.5 * sum(CASE WHEN q > 0 THEN q * log2(q / ((p + q) / 2)) ELSE 0 END))) AS d
  FROM wide GROUP BY ALL"""


def drift(log_id: str, case_type: str | None = None, *, window: str = "month", window_cases: int = 500,
          min_cases: int = 30, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    if window not in WINDOWS:
        raise workspace.EventLogWorkspaceError("window must be week, month or cases")
    if not 20 <= int(window_cases) <= 100_000:
        raise workspace.EventLogWorkspaceError("window_cases must be between 20 and 100000")
    if not 10 <= int(min_cases) <= 10_000:
        raise workspace.EventLogWorkspaceError("min_cases must be between 10 and 10000")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        bucket = (f"date_trunc('{window}', min(ts))" if window != "cases"
                  else f"(row_number() OVER (ORDER BY min(ts), case_id) - 1) // {int(window_cases)}")
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE dw0 AS SELECT case_id, {bucket} AS bucket,
              epoch(max(ts) - min(ts)) AS duration FROM ce GROUP BY case_id""")
        conn.execute("CREATE OR REPLACE TEMP TABLE dw AS SELECT *, dense_rank() OVER (ORDER BY bucket) - 1 AS w FROM dw0")
        buckets = conn.execute("""SELECT bucket, count(*), quantile_cont(duration, 0.5) FROM dw
            GROUP BY bucket ORDER BY bucket""").fetchall()
        if len(buckets) > MAX_WINDOWS:
            raise workspace.EventLogWorkspaceError(
                f"{len(buckets)} windows is more than {MAX_WINDOWS}; choose a longer window or more cases per window")
        # Distinct paths per case: the case contains A → B at least once.
        conn.execute("""CREATE OR REPLACE TEMP TABLE ce_edges AS SELECT DISTINCT case_id, to_json([src, activity])::VARCHAR AS edge
            FROM (SELECT case_id, activity, lag(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS src FROM ce)
            WHERE src IS NOT NULL""")
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE dcase AS SELECT case_id, w FROM dw
            QUALIFY row_number() OVER (PARTITION BY w ORDER BY hash(case_id)) <= {SAMPLE_PER_WINDOW}""")
        # The statistical comparison uses dcase (at most SAMPLE_PER_WINDOW
        # deterministic cases per window), not all cases in dw. Disclose the
        # actual analysis denominator rather than only the configured cap.
        sampled_by_window = dict(conn.execute(
            "SELECT w, count(*) FROM dcase GROUP BY w").fetchall())
        windows = [{"window": str(b) if window == "cases" else b.isoformat()[:10], "cases": n,
                    "sampled_cases": sampled_by_window.get(i, 0),
                    "sampling_truncated": sampled_by_window.get(i, 0) < n,
                    "median_case_duration_seconds": med}
                   for i, (b, n, med) in enumerate(buckets)]
        if window == "cases":
            for i, w in enumerate(windows):
                w["window"] = f"cases {i * int(window_cases) + 1}–{i * int(window_cases) + w['cases']}"
        # The statistical test operates on dcase, not every eligible case in
        # dw. Requiring support from the full population could admit a test
        # with fewer than min_cases actual sampled cases on either side.
        tested = [i for i in range(len(buckets) - 1)
                  if sampled_by_window.get(i, 0) >= min_cases
                  and sampled_by_window.get(i + 1, 0) >= min_cases]
        boundaries = []
        if tested:
            conn.execute("CREATE OR REPLACE TEMP TABLE pairs (pair INTEGER)")
            conn.executemany("INSERT INTO pairs VALUES (?)", [[i] for i in tested])
            # Observed: window i (side 0) against window i + 1 (side 1).
            conn.execute("""CREATE OR REPLACE TEMP TABLE pc AS SELECT p.pair, c.case_id, (c.w - p.pair) AS side
                FROM pairs p JOIN dcase c ON c.w IN (p.pair, p.pair + 1)""")
            conn.execute("""CREATE OR REPLACE TEMP TABLE obs_counts AS SELECT pc.pair, e.edge, pc.side, count(*) AS n
                FROM pc JOIN ce_edges e USING (case_id) GROUP BY ALL""")
            observed = dict(conn.execute(_JSD.format(key="pair", src="obs_counts")).fetchall())
            # Permutations keep each side's size: rank the pooled cases by a seeded hash.
            conn.execute(f"""CREATE OR REPLACE TEMP TABLE perm AS SELECT pc.pair, r.k, pc.case_id,
                  CASE WHEN row_number() OVER (PARTITION BY pc.pair, r.k ORDER BY hash(pc.case_id, r.k))
                            <= count(*) FILTER (WHERE pc.side = 0) OVER (PARTITION BY pc.pair, r.k) THEN 0 ELSE 1 END AS side
                FROM pc, range({PERMUTATIONS}) r(k)""")
            conn.execute("""CREATE OR REPLACE TEMP TABLE perm_counts AS SELECT p.pair * 1000 + p.k AS pk, p.pair,
                  e.edge, p.side, count(*) AS n FROM perm p JOIN ce_edges e USING (case_id) GROUP BY ALL""")
            null = conn.execute(f"""SELECT pk // 1000 AS pair, count(*) FILTER (WHERE j.d >= o.d - 1e-12),
                  avg(j.d), stddev_samp(j.d) FROM ({_JSD.format(key="pk", src="perm_counts")}) j
                  JOIN (SELECT unnest(?) AS pair, unnest(?) AS d) o ON o.pair = j.pk // 1000 GROUP BY 1""",
                                [list(observed), list(observed.values())]).fetchall()
            stats = {pair: (exceed, mean, sd) for pair, exceed, mean, sd in null}
            threshold = ALPHA / len(tested)
            for i in tested:
                exceed, mean, sd = stats.get(i, (0, 0.0, 0.0))
                d = observed.get(i, 0.0)
                if exceed or not sd:
                    p_value, method = (1 + exceed) / (PERMUTATIONS + 1), "permutation"
                else:
                    # Beyond every permutation: a normal tail on the permutation null resolves small p-values.
                    p_value, method = 0.5 * math.erfc((d - mean) / sd / math.sqrt(2)), "normal tail of permutation null"
                entry = {"before": windows[i]["window"], "after": windows[i + 1]["window"],
                         "distance": round(d, 6), "p_value": p_value, "p_value_method": method,
                         "change_point": p_value < threshold and d >= MIN_DISTANCE}
                if entry["change_point"]:
                    moved = conn.execute("""WITH s AS (SELECT c.w, e.edge, count(*) AS n FROM dcase c
                          JOIN ce_edges e USING (case_id) WHERE c.w IN (?, ?) GROUP BY ALL),
                        sz AS (SELECT w, count(*) AS n FROM dcase WHERE w IN (?, ?) GROUP BY w)
                        SELECT e.edge, coalesce(max(s.n) FILTER (WHERE s.w = ?), 0) / any_value(a.n),
                               coalesce(max(s.n) FILTER (WHERE s.w = ?), 0) / any_value(b.n)
                        FROM (SELECT DISTINCT edge FROM s) e LEFT JOIN s USING (edge),
                             (SELECT n FROM sz WHERE w = ?) a, (SELECT n FROM sz WHERE w = ?) b
                        GROUP BY e.edge""", [i, i + 1, i, i + 1, i, i + 1, i, i + 1]).fetchall()
                    moved.sort(key=lambda r: (-abs(r[2] - r[1]), r[0]))
                    # Decode only after grouping: JSON pairs prevent activity names
                    # containing the display separator from colliding.
                    shown = []
                    for encoded, before, after in moved[:5]:
                        source, target = json.loads(encoded)
                        label = (f"{source} → {target}" if " → " not in source and " → " not in target
                                 else json.dumps([source, target], ensure_ascii=False))
                        shown.append({"path": label, "from": source, "to": target,
                                      "case_share_before": round(before, 6),
                                      "case_share_after": round(after, 6)})
                    entry["paths_that_moved"] = shown
                boundaries.append(entry)
        return {
            "settings": {"window": window, "window_cases": int(window_cases) if window == "cases" else None,
                         "min_cases": int(min_cases), "permutations": PERMUTATIONS,
                         "sample_per_window": SAMPLE_PER_WINDOW, "alpha": ALPHA,
                         "min_distance": MIN_DISTANCE},
            "cohort": cohorts.describe(chosen),
            "windows": windows,
            "boundaries": boundaries,
            "boundaries_tested": len(tested),
            "boundaries_skipped_small_windows": max(0, len(buckets) - 1 - len(tested)),
            "change_points": [b for b in boundaries if b["change_point"]],
            "interpretation": ("A change point means the recorded paths differ between two adjacent windows more "
                               "than random splits of the same cases do (permutation test, Bonferroni-corrected). "
                               "It does not say why; a change in how events are recorded looks the same."),
        }
    return workspace._query(log_id, case_type, run)


__all__ = ["drift", "WINDOWS"]
