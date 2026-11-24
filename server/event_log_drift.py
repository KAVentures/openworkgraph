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
import re
from hashlib import sha256
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


def _validate_window(window: str, window_cases: int) -> None:
    if window not in WINDOWS:
        raise workspace.EventLogWorkspaceError("window must be week, month or cases")
    # FastAPI validates typed query parameters, but direct callers (including
    # internal integrations) must receive the same bounded, fail-closed contract.
    # int(value) would silently truncate fractional values and raise raw
    # ValueError/TypeError/OverflowError for malformed or nonfinite inputs.
    if not isinstance(window_cases, int) or isinstance(window_cases, bool) or not 20 <= window_cases <= 100_000:
        raise workspace.EventLogWorkspaceError("window_cases must be an integer between 20 and 100000")


def _windows(conn, window: str, window_cases: int) -> None:
    bucket = (f"date_trunc('{window}', min(ts))" if window != "cases"
              else f"(row_number() OVER (ORDER BY min(ts), case_id) - 1) // {window_cases}")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE dw0 AS SELECT case_id, {bucket} AS bucket,
          epoch(max(ts) - min(ts)) AS duration FROM ce GROUP BY case_id""")
    conn.execute("CREATE OR REPLACE TEMP TABLE dw AS SELECT *, dense_rank() OVER (ORDER BY bucket) - 1 AS w FROM dw0")
    count = conn.execute("SELECT count(DISTINCT w) FROM dw").fetchone()[0]
    if count > MAX_WINDOWS:
        raise workspace.EventLogWorkspaceError(
            f"{count} windows is more than {MAX_WINDOWS}; choose a longer window or more cases per window")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE dcase AS SELECT case_id, w FROM dw
        QUALIFY row_number() OVER (PARTITION BY w ORDER BY hash(case_id), case_id) <= {SAMPLE_PER_WINDOW}""")


def _window_label(bucket, count: int, window: str, window_cases: int) -> str:
    if window == "cases":
        start = bucket * window_cases
        return f"cases {start + 1}–{start + count}"
    return bucket.isoformat()[:10]


def _sample_basis(conn, log_id, case_type, window, window_size, chosen) -> list[Any]:
    # _query has already applied these settings under the same workspace lock.
    # Window membership can survive an append or remapping that changes its paths.
    revision = conn.execute("SELECT coalesce(max(batch), 0) FROM log_appends WHERE log_id = ?",
                            [log_id]).fetchone()[0]
    mapping = conn.execute("SELECT definition FROM activity_mappings WHERE log_id = ? AND applied",
                           [log_id]).fetchone()
    hidden = [name for (name,) in conn.execute("SELECT activity FROM hidden_activities WHERE log_id = ? ORDER BY 1",
                                             [log_id]).fetchall()]
    return [log_id, workspace._case_type(conn, log_id, case_type), revision, mapping, hidden,
            window, window_size if window == 'cases' else None, cohorts.describe(chosen)]


def _sample_sha256(basis, index, label, total, median, keys) -> str:
    payload = [basis, index, label, total, median, keys]
    return sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def drift(log_id: str, case_type: str | None = None, *, window: str = "month", window_cases: int = 500,
          min_cases: int = 30, cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    _validate_window(window, window_cases)
    if not isinstance(min_cases, int) or isinstance(min_cases, bool) or not 10 <= min_cases <= 10_000:
        raise workspace.EventLogWorkspaceError("min_cases must be an integer between 10 and 10000")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        _windows(conn, window, window_cases)
        buckets = conn.execute("""SELECT bucket, count(*), quantile_cont(duration, 0.5) FROM dw
            GROUP BY bucket ORDER BY bucket""").fetchall()
        # Distinct paths per case: the case contains A → B at least once.
        conn.execute("""CREATE OR REPLACE TEMP TABLE ce_edges AS SELECT DISTINCT case_id, to_json([src, activity])::VARCHAR AS edge
            FROM (SELECT case_id, activity, lag(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS src FROM ce)
            WHERE src IS NOT NULL""")
        # The statistical comparison uses dcase (at most SAMPLE_PER_WINDOW
        # deterministic cases per window), not all cases in dw. Disclose the
        # actual analysis denominator rather than only the configured cap.
        keys_by_window = dict(conn.execute(
            "SELECT w, list(case_id ORDER BY case_id) FROM dcase GROUP BY w").fetchall())
        sampled_by_window = {i: len(keys) for i, keys in keys_by_window.items()}
        basis = _sample_basis(conn, log_id, case_type, window, window_cases, chosen)
        # Single-event cases provide no directly-follows observations. Even
        # when the total sampled case count meets min_cases, a distribution
        # supported by only one or a few cases is too sparse for this test.
        path_cases_by_window = dict(conn.execute(
            "SELECT c.w, count(DISTINCT c.case_id) FROM dcase c "
            "JOIN ce_edges e USING (case_id) GROUP BY c.w").fetchall())
        windows = []
        for i, (bucket, total, median) in enumerate(buckets):
            label = _window_label(bucket, total, window, window_cases)
            keys = keys_by_window.get(i, [])
            windows.append({"window": label, "cases": total, "sampled_cases": len(keys),
                            "sampled_cases_with_paths": path_cases_by_window.get(i, 0),
                            "sampling_truncated": len(keys) < total, "median_case_duration_seconds": median,
                            "sample_sha256": _sample_sha256(basis, i, label, total, median, keys)})
        # The statistical test operates on dcase, not every eligible case in
        # dw. Require min_cases actual sampled cases AND min_cases sampled
        # path-bearing cases per side; single-event cases are not evidence
        # for the directly-follows distribution.
        tested = [i for i in range(len(buckets) - 1)
                  if sampled_by_window.get(i, 0) >= min_cases
                  and sampled_by_window.get(i + 1, 0) >= min_cases
                  and path_cases_by_window.get(i, 0) >= min_cases
                  and path_cases_by_window.get(i + 1, 0) >= min_cases]
        # Disclose omitted adjacent comparisons as well as the aggregate count.
        # These are sampled support counts, never case IDs or raw evidence.
        tested_set = set(tested)
        boundaries_not_tested = []
        for i in range(len(windows) - 1):
            if i in tested_set:
                continue
            before_n = sampled_by_window.get(i, 0)
            after_n = sampled_by_window.get(i + 1, 0)
            before_low, after_low = before_n < min_cases, after_n < min_cases
            omitted = {
                "before": windows[i]["window"], "after": windows[i + 1]["window"],
                "sampled_cases_before": before_n, "sampled_cases_after": after_n,
            }
            if before_low or after_low:
                # Keep the established small-window response shape unchanged.
                omitted["insufficient_side"] = (
                    "both" if before_low and after_low else ("before" if before_low else "after"))
            else:
                before_paths = path_cases_by_window.get(i, 0)
                after_paths = path_cases_by_window.get(i + 1, 0)
                no_paths = not before_paths or not after_paths
                omitted.update({
                    "insufficient_side": None,
                    "reason": ("no_directly_follows_paths" if no_paths
                               else "insufficient_path_support"),
                    "sampled_cases_with_paths_before": before_paths,
                    "sampled_cases_with_paths_after": after_paths,
                })
                if no_paths:
                    omitted["missing_paths_side"] = (
                        "both" if not before_paths and not after_paths
                        else ("before" if not before_paths else "after"))
                else:
                    omitted["insufficient_path_support_side"] = (
                        "both" if before_paths < min_cases and after_paths < min_cases
                        else ("before" if before_paths < min_cases else "after"))
            boundaries_not_tested.append(omitted)
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
            "settings": {"window": window, "window_cases": window_cases if window == "cases" else None,
                         "min_cases": min_cases, "permutations": PERMUTATIONS,
                         "sample_per_window": SAMPLE_PER_WINDOW, "alpha": ALPHA,
                         "min_distance": MIN_DISTANCE},
            "cohort": cohorts.describe(chosen),
            "windows": windows,
            "boundaries": boundaries,
            "boundaries_tested": len(tested),
            # Each omitted boundary has exactly one reason; sparse path
            # support must not also count as a small sampled-case window.
            "boundaries_skipped_small_windows": sum(
                b.get("reason") is None for b in boundaries_not_tested),
            "boundaries_skipped_no_paths": sum(
                b.get("reason") == "no_directly_follows_paths" for b in boundaries_not_tested),
            "boundaries_skipped_sparse_paths": sum(
                b.get("reason") == "insufficient_path_support" for b in boundaries_not_tested),
            "boundaries_not_tested": boundaries_not_tested,
            "change_points": [b for b in boundaries if b["change_point"]],
            "interpretation": ("A change point means the recorded paths differ between two adjacent windows more "
                               "than random splits of the same cases do (permutation test, Bonferroni-corrected). "
                               "It does not say why; a change in how events are recorded looks the same."),
        }
    return workspace._query(log_id, case_type, run)


def window_cases(log_id: str, case_type: str | None = None, *, window_index: int,
                 window: str = "month", window_size: int = 500, source: str | None = None,
                 destination: str | None = None, after: str | None = None, limit: int = 50,
                 expected_window: str | None = None, expected_sample_sha256: str | None = None,
                 cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    _validate_window(window, window_size)
    if type(window_index) is not int or not 0 <= window_index < MAX_WINDOWS:
        raise workspace.EventLogWorkspaceError("choose a valid drift window index")
    if (source is None) != (destination is None) or any(
            not isinstance(value, str) or not value or len(value) > 200
            for value in (source, destination) if value is not None):
        raise workspace.EventLogWorkspaceError("a path needs both exact activity labels")
    if after is not None and (not isinstance(after, str) or not after or len(after) > 200):
        raise workspace.EventLogWorkspaceError("invalid case key cursor")
    if expected_window is not None and (not isinstance(expected_window, str) or len(expected_window) > 80):
        raise workspace.EventLogWorkspaceError("invalid drift window label")
    if expected_sample_sha256 is not None and (not isinstance(expected_sample_sha256, str) or
                                               not re.fullmatch(r"[0-9a-f]{64}", expected_sample_sha256)):
        raise workspace.EventLogWorkspaceError("invalid drift sample digest")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise workspace.EventLogWorkspaceError("page size must be a whole number from 1 to 100")
    chosen = cohorts.parse_cohort(cohort)

    def run(conn):
        cohorts.restrict(conn, chosen, log_id)
        _windows(conn, window, window_size)
        bucket, total, median = conn.execute("SELECT first(bucket), count(*), quantile_cont(duration, 0.5) "
                                             "FROM dw WHERE w = ?", [window_index]).fetchone()
        if not total:
            raise workspace.EventLogWorkspaceError("this drift window no longer exists")
        label = _window_label(bucket, total, window, window_size)
        if expected_window is not None and expected_window != label:
            raise workspace.EventLogWorkspaceError("this drift window changed; run the analysis again")
        keys = [key for (key,) in conn.execute("SELECT case_id FROM dcase WHERE w = ? ORDER BY case_id",
                                              [window_index]).fetchall()]
        basis = _sample_basis(conn, log_id, case_type, window, window_size, chosen)
        digest = _sample_sha256(basis, window_index, label, total, median, keys)
        if expected_sample_sha256 is not None and expected_sample_sha256 != digest:
            raise workspace.EventLogWorkspaceError("this drift sample changed; run the analysis again")
        selection, args = "", []
        if source is not None:
            selection = """ AND case_id IN (
                SELECT case_id FROM (
                    SELECT case_id, activity, lag(activity) OVER (PARTITION BY case_id ORDER BY ts, event_id) AS src
                    FROM ce JOIN dcase USING (case_id) WHERE w = ?
                ) AS edges WHERE src = ? AND activity = ?
            )"""
            args = [window_index, source, destination]
        conn.execute("CREATE TEMP TABLE drift_evidence AS SELECT case_id, duration FROM dw WHERE w = ? "
                     "AND case_id IN (SELECT case_id FROM dcase)" + selection,
                     [window_index, *args])
        matching = conn.execute("SELECT count(*) FROM drift_evidence").fetchone()[0]
        where, cursor = (" WHERE case_id > ?", [after]) if after is not None else ("", [])
        rows = conn.execute("SELECT case_id, duration FROM drift_evidence" + where + " ORDER BY case_id LIMIT ?",
                            [*cursor, limit + 1]).fetchall()
        return {"window": label, "window_index": window_index,
                "window_cases": total, "sampled_cases": len(keys), "sample_sha256": digest, "matching_cases": matching,
                "cases": [{"case_id": key, "duration_seconds": duration} for key, duration in rows[:limit]],
                "next_after": rows[limit-1][0] if len(rows) > limit else None}

    return workspace._query(log_id, case_type, run)


__all__ = ["drift", "window_cases", "WINDOWS"]
