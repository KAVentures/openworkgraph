"""Censoring-aware remaining-time estimates for open cases (#370 R19).

Learning only from completed cases is biased: the cases still open at the
end of a log are disproportionately the long ones, so completed history
under-represents long remaining times, and the most recent period worst of
all. Here every case contributes. A completed case's earlier events give an
observed remaining time; an open case's events give a lower bound (the case
had not finished by the time the log ends), a right-censored observation. A
Kaplan–Meier estimator per comparable state (latest activity and step, with
the same fallbacks as the completed-case estimator) turns both into a
survival curve S(t), the share of cases with more than t remaining.

An open case has already been idle since its latest event, so its estimate is
conditioned on that: the remaining time from now has quantiles where
S(idle + t) / S(idle) falls to 1 - q. When too many comparable cases are
still open for a quantile to be reached, the estimate is withheld and a lower
bound is given instead, rather than guessed.

The backtest is an as-of replay: pretend the log ended at a cutoff, learn
from what was known then (cases open at the cutoff are censored there), and
predict the cases that were in progress at the cutoff. Cases that finished
later are scored; cases still open now are counted, not dropped silently.
The completed-case estimator is scored on the same cases for comparison.
"""
from __future__ import annotations

from typing import Any

QUANTILES = (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))
_KEY = "g, activity, step"


def observations(conn, cutoff: Any | None = None) -> None:
    """``so``: one remaining-time observation per prefix, observed or censored at the horizon.

    Without ``cutoff`` the horizon is the end of the log (the ``pc`` table's open cases are censored).
    With it, only events up to the cutoff are known and cases not finished by then are censored there."""
    if cutoff is None:
        conn.execute("""CREATE OR REPLACE TEMP TABLE so AS
            SELECT e.case_id, e.activity, least(e.n, ?) AS step,
                   CASE WHEN c.complete THEN epoch(c.ended - e.ts) ELSE epoch(h.at - e.ts) END AS duration,
                   c.complete AS observed
            FROM (SELECT case_id, activity, ts, row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS n
                  FROM ce) e JOIN pc c USING (case_id), (SELECT max(ts) AS at FROM ce) h
            WHERE NOT c.complete OR e.n < c.events""", [_max_step()])
        return
    conn.execute("""CREATE OR REPLACE TEMP TABLE so AS
        SELECT e.case_id, e.activity, least(e.n, ?) AS step,
               CASE WHEN c.complete AND c.ended <= ? THEN epoch(c.ended - e.ts) ELSE epoch(?::TIMESTAMP - e.ts) END AS duration,
               c.complete AND c.ended <= ? AS observed
        FROM (SELECT case_id, activity, ts, row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS n,
                     count(*) OVER (PARTITION BY case_id) AS known
              FROM ce WHERE ts <= ?) e JOIN pc c USING (case_id)
        WHERE NOT (c.complete AND c.ended <= ?) OR e.n < e.known""", [_max_step(), cutoff, cutoff, cutoff, cutoff, cutoff])


def _max_step() -> int:
    from .event_log_predictions import MAX_STEP
    return MAX_STEP


def fit(conn) -> None:
    """Kaplan–Meier curves ``km`` and support ``ks`` for every state and fallback level of ``so``.

    Levels: g = 0 latest activity and step, 1 latest activity, 3 all cases (as in the completed-case
    estimator). Keys never hold NULL so they can be joined with plain equality."""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE sg AS
        SELECT 0 AS g, activity, step, case_id, duration, observed FROM so
        UNION ALL SELECT 1, activity, -1, case_id, duration, observed FROM so
        UNION ALL SELECT 3, '', -1, case_id, duration, observed FROM so""")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE ks AS SELECT {_KEY}, count(DISTINCT case_id) AS cases,
          count(DISTINCT case_id) FILTER (WHERE observed) AS observed_cases,
          count(DISTINCT case_id) FILTER (WHERE NOT observed) AS censored_cases, max(duration) AS longest
        FROM sg GROUP BY {_KEY}""")
    # Deaths d and censorings c at each time; n at risk counts everything lasting at least t.
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE kt AS SELECT {_KEY}, duration AS t,
          count(*) FILTER (WHERE observed) AS d, count(*) AS m FROM sg GROUP BY {_KEY}, duration""")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE km AS SELECT {_KEY}, t, s FROM (
        SELECT {_KEY}, t, d,
               CASE WHEN bool_or(d >= n) OVER w THEN 0.0
                    ELSE exp(sum(CASE WHEN d < n THEN ln(1 - d / n) ELSE 0 END) OVER w) END AS s,
               bool_or(d >= n) OVER (PARTITION BY {_KEY} ORDER BY t ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS was_zero
        FROM (SELECT *, sum(m) OVER (PARTITION BY {_KEY} ORDER BY t DESC) AS n FROM kt)
        WINDOW w AS (PARTITION BY {_KEY} ORDER BY t ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      WHERE d > 0 AND NOT coalesce(was_zero, false)""")


def estimate(conn, source: str, support: int) -> None:
    """``{source}_sv``: conditional remaining-time quantiles for every row of ``source`` (id, activity, step, idle).

    The most specific state with at least ``support`` distinct cases is used. A quantile the curve never
    reaches is NULL (too many comparable cases are still open), and ``at_least`` is the longest remaining
    time seen in that state beyond the idle time so far."""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {source}_k AS SELECT t.*,
          CASE WHEN s1.cases >= ? THEN 0 WHEN s2.cases >= ? THEN 1 WHEN s0.cases >= ? THEN 3 END AS g,
          CASE WHEN s1.cases >= ? THEN t.activity WHEN s2.cases >= ? THEN t.activity ELSE '' END AS k_activity,
          CASE WHEN s1.cases >= ? THEN t.step ELSE -1 END AS k_step
        FROM {source} t
        LEFT JOIN ks s1 ON s1.g = 0 AND s1.activity = t.activity AND s1.step = t.step
        LEFT JOIN ks s2 ON s2.g = 1 AND s2.activity = t.activity
        LEFT JOIN ks s0 ON s0.g = 3""", [support] * 6)
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {source}_s AS SELECT k.*, coalesce(m.s, 1.0) AS s_idle
        FROM {source}_k k ASOF LEFT JOIN km m
          ON m.g = k.g AND m.activity = k.k_activity AND m.step = k.k_step AND k.idle >= m.t""")
    joins, cols = [], []
    for name, q in QUANTILES:
        # S strictly decreases along km, so the row with the largest s at or below the target is the first time
        # the curve reaches it. A case idle past every comparable death (s_idle = 0) gets no estimate.
        joins.append(f"""ASOF LEFT JOIN km {name}
          ON {name}.g = b.g AND {name}.activity = b.k_activity AND {name}.step = b.k_step
         AND b.s_idle * {1 - q!r} >= {name}.s""")
        cols.append(f"CASE WHEN b.s_idle > 0 THEN {name}.t - b.idle END AS {name}")
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {source}_sv AS SELECT b.*, {', '.join(cols)},
          CASE WHEN b.g = 0 THEN 'same_activity_and_step' WHEN b.g = 1 THEN 'same_activity'
               WHEN b.g = 3 THEN 'all_cases' END AS basis,
          s.cases, s.observed_cases, s.censored_cases, greatest(s.longest - b.idle, 0) AS at_least
        FROM {source}_s b {' '.join(joins)}
        LEFT JOIN ks s ON s.g = b.g AND s.activity = b.k_activity AND s.step = b.k_step""")


def backtest(conn, cutoff: Any, support: int) -> dict[str, Any]:
    """As-of replay at ``cutoff``: learn what was known then, predict cases in progress then."""
    observations(conn, cutoff)
    fit(conn)
    conn.execute("""CREATE OR REPLACE TEMP TABLE sb AS SELECT c.case_id,
          arg_max(e.activity, struct_pack(t := e.ts, e := e.event_id)) AS activity,
          least(count(*), ?) AS step, epoch(?::TIMESTAMP - max(e.ts)) AS idle,
          CASE WHEN c.complete THEN epoch(c.ended - ?::TIMESTAMP) END AS truth,
          epoch(max_ts.at - ?::TIMESTAMP) AS known_open
        FROM ce e JOIN pc c USING (case_id), (SELECT max(ts) AS at FROM ce) max_ts
        WHERE e.ts <= ? AND NOT (c.complete AND c.ended <= ?)
        GROUP BY c.case_id, c.complete, c.ended, max_ts.at""", [_max_step(), cutoff, cutoff, cutoff, cutoff, cutoff])
    estimate(conn, "sb", support)
    # The completed-case estimator on the same cases: learn from cases finished by the cutoff, measure from now.
    conn.execute("""CREATE OR REPLACE TEMP TABLE sbc AS SELECT case_id, activity, step FROM sb""")
    from .event_log_predictions import _choose, _train
    _train(conn, "ended <= ?", [cutoff])
    _choose(conn, "sbc", support)
    (cases, estimated, abstained, scored, still_open, mae, inside, completed_mae, completed_inside,
     beyond_p90) = conn.execute("""SELECT count(*), count(*) FILTER (WHERE v.basis IS NOT NULL),
          count(*) FILTER (WHERE v.basis IS NOT NULL AND v.q50 IS NULL),
          count(*) FILTER (WHERE v.q50 IS NOT NULL AND v.truth IS NOT NULL AND c.basis IS NOT NULL),
          count(*) FILTER (WHERE v.truth IS NULL),
          avg(abs(v.q50 - v.truth)) FILTER (WHERE v.q50 IS NOT NULL AND v.truth IS NOT NULL AND c.basis IS NOT NULL),
          avg(CASE WHEN v.truth BETWEEN coalesce(v.q10, 0) AND v.q90 THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE v.q50 IS NOT NULL AND v.q90 IS NOT NULL AND v.truth IS NOT NULL AND c.basis IS NOT NULL),
          avg(abs(greatest(c.q50 - v.idle, 0) - v.truth))
            FILTER (WHERE v.q50 IS NOT NULL AND v.truth IS NOT NULL AND c.basis IS NOT NULL),
          avg(CASE WHEN v.truth BETWEEN greatest(c.q10 - v.idle, 0) AND greatest(c.q90 - v.idle, 0) THEN 1.0 ELSE 0.0 END)
            FILTER (WHERE v.q50 IS NOT NULL AND v.q90 IS NOT NULL AND v.truth IS NOT NULL AND c.basis IS NOT NULL),
          count(*) FILTER (WHERE v.truth IS NULL AND v.q90 IS NOT NULL AND v.known_open > v.q90)
        FROM sb_sv v JOIN sbc_est c USING (case_id)""").fetchone()
    out = {"design": "as_of_replay", "cutoff": cutoff.isoformat() + "Z", "cases_in_progress_at_cutoff": cases,
           "estimated_cases": estimated, "abstained_cases": abstained, "scored_cases": scored,
           "still_open_unscored_cases": still_open, "still_open_already_beyond_p90": beyond_p90}
    if not scored:
        return {"available": False, **out,
                "reason": "no case in progress at the cutoff has both estimates and has finished since"}
    return {"available": True, **out, "mean_absolute_error_seconds": mae, "p10_p90_coverage": inside,
            "expected_p10_p90_coverage": 0.8,
            "completed_cases_estimator": {"mean_absolute_error_seconds": completed_mae, "p10_p90_coverage": completed_inside},
            "improvement_over_completed_cases_estimator": 1 - mae / completed_mae if completed_mae else None,
            "scoring_note": ("Only cases that have finished since the cutoff can be scored; they are the shorter ones, "
                             "so these errors favour short remaining times. Still-open cases are counted separately.")}
