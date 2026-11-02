"""Calibration and drift monitoring for remaining-time and outcome-risk predictions (#370 R19).

The prediction backtest scores one cutoff. Monitoring asks whether prediction quality holds over time:

- **rolling windows:** completed cases are split by when they ended into consecutive windows. Each window is
  predicted from a model trained only on cases that ended before it starts, the same estimator and fallbacks as
  live predictions;
- **per window:** mean absolute error of the median remaining time against the naive baseline, how often the
  true remaining time fell inside the 10th–90th percentile range (nominally 80%) with a 95% interval, and for an
  outcome the Brier score against the base rate;
- **flags:** in the latest window, coverage whose interval lies below 80% (ranges too narrow) or above it (too
  wide) by at least 5 points, and error more than 25% above the first window. Each window's interval is a 95%
  interval, so across many windows one can fall outside by chance; flags therefore use the latest window only;
- **reliability:** in the latest window, predicted outcome risk by fifths against the share that happened;
- **recalibration proposal:** the factor by which to widen or narrow the ranges around the median so that 80% of
  the latest window would have been covered, checked on the window before it. It is proposed for review, never
  applied to predictions.

Prefixes of one case are not independent, so intervals treat each case as one observation (its share of covered
prefixes). Monitoring describes this log's history; it does not establish that future predictions hold.
"""
from __future__ import annotations

import math
from typing import Any

from pydantic import Field, StrictInt, ValidationError

from . import event_log_predictions as predictions
from . import event_log_workspace as workspace

NOMINAL_COVERAGE = 0.8
COVERAGE_FLOOR = 0.05          # a flag needs this gap from nominal, not only an interval that excludes it
ERROR_RISE = 1.25
MIN_CASES_PER_WINDOW = 10


class MonitoringSpec(predictions.PredictionSpec):
    windows: StrictInt = Field(default=6, ge=2, le=12)


def parse(spec: Any) -> MonitoringSpec:
    if isinstance(spec, dict) and "backtest_share" in spec:
        raise workspace.EventLogWorkspaceError("monitoring splits the log into windows; backtest_share does not apply")
    try:
        parsed = MonitoringSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "monitoring settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    predictions.parse(parsed.model_dump(exclude={"windows"}))     # the same cross-field checks as predictions
    if parsed.estimator != "completed_cases":
        raise workspace.EventLogWorkspaceError("monitoring scores the completed-cases estimator")
    return parsed


def _mean_ci(values: list[float]) -> tuple[float, list[float]]:
    n = len(values)
    mean = sum(values) / n
    if n < 2:
        return mean, [mean, mean]
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = 1.959964 * sd / math.sqrt(n)
    return mean, [max(0.0, mean - half), min(1.0, mean + half)]


def _score(conn, low, high, has_outcome: bool) -> dict[str, Any]:
    """Train on cases that ended at or before ``low``; score prefixes of cases that ended in (low, high]."""
    predictions._train(conn, "ended <= ?", [low])
    conn.execute("""CREATE OR REPLACE TEMP TABLE pt AS SELECT case_id, activity, step, remaining, outcome FROM pp
        WHERE ended > ? AND ended <= ?""", [low, high])
    predictions._choose(conn, "pt", conn.execute("SELECT getvariable('owg_support')").fetchone()[0])
    tests, scored, mae, naive, brier, base_brier = conn.execute("""SELECT count(*), count(*) FILTER (WHERE basis IS NOT NULL),
          avg(abs(q50 - remaining)) FILTER (WHERE basis IS NOT NULL),
          avg(abs(naive_q50 - remaining)) FILTER (WHERE basis IS NOT NULL),
          avg(power(rate - CAST(outcome AS DOUBLE), 2)) FILTER (WHERE basis IS NOT NULL),
          avg(power(base_rate - CAST(outcome AS DOUBLE), 2)) FILTER (WHERE basis IS NOT NULL) FROM pt_est""").fetchone()
    per_case = [r[0] for r in conn.execute("""SELECT avg(CASE WHEN remaining BETWEEN q10 AND q90 THEN 1.0 ELSE 0.0 END)
        FROM pt_est WHERE basis IS NOT NULL GROUP BY case_id ORDER BY case_id""").fetchall()]
    out: dict[str, Any] = {"from": low.isoformat() + "Z", "to": high.isoformat() + "Z", "test_prefixes": tests,
                           "scored_prefixes": scored, "scored_cases": len(per_case)}
    if len(per_case) < MIN_CASES_PER_WINDOW:
        return {**out, "scored": False, "reason": f"fewer than {MIN_CASES_PER_WINDOW} cases could be predicted"}
    coverage, ci = _mean_ci(per_case)
    out.update(scored=True, mean_absolute_error_seconds=mae, naive_mean_absolute_error_seconds=naive,
               improvement_over_naive=1 - mae / naive if naive else None,
               p10_p90_coverage=round(coverage, 6), p10_p90_coverage_ci95=[round(x, 6) for x in ci],
               coverage_flag=("ranges_too_narrow" if ci[1] < NOMINAL_COVERAGE and coverage <= NOMINAL_COVERAGE - COVERAGE_FLOOR
                              else "ranges_too_wide" if ci[0] > NOMINAL_COVERAGE and coverage >= NOMINAL_COVERAGE + COVERAGE_FLOOR
                              else None))
    if has_outcome:
        out.update(brier_score=brier, base_rate_brier_score=base_brier)
    return out


def _needed_factors(conn) -> list[float]:
    """Per prefix, the smallest scaling of its range around the median that would have covered it."""
    rows = conn.execute("""SELECT remaining, q10, q50, q90 FROM pt_est WHERE basis IS NOT NULL
        ORDER BY case_id, activity, step, remaining""").fetchall()
    factors = []
    for remaining, q10, q50, q90 in rows:
        if remaining >= q50:
            span = q90 - q50
            need = (remaining - q50) / span if span > 0 else (0.0 if remaining == q50 else math.inf)
        else:
            span = q50 - q10
            need = (q50 - remaining) / span if span > 0 else math.inf
        factors.append(need)
    return factors


def _coverage_at(factors: list[float], factor: float) -> float | None:
    return sum(1 for f in factors if f <= factor) / len(factors) if factors else None


def _reliability(conn) -> list[dict[str, Any]]:
    rows = conn.execute("""SELECT least(floor(rate * 5), 4) AS bin, count(*), avg(rate), avg(CAST(outcome AS DOUBLE))
        FROM pt_est WHERE basis IS NOT NULL AND rate IS NOT NULL GROUP BY 1 ORDER BY 1""").fetchall()
    return [{"predicted_from": b / 5, "predicted_to": (b + 1) / 5, "prefixes": n,
             "mean_predicted": round(p, 6), "observed_share": round(o, 6)} for b, n, p, o in rows]


def monitor(log_id: str, spec: Any) -> dict[str, Any]:
    settings = parse(spec)
    has_outcome = settings.outcome is not None

    def run(conn):
        predictions._prepare(conn, settings, log_id)
        conn.execute("SET VARIABLE owg_support = ?", [settings.min_support])
        quantiles = [k / (settings.windows + 1) for k in range(1, settings.windows + 1)]
        cuts = conn.execute("SELECT quantile_disc(ended, ?), max(ended) FROM pc WHERE complete",
                            [quantiles]).fetchone()
        if cuts[0] is None:
            raise workspace.EventLogWorkspaceError("monitoring needs completed cases; check the end activities")
        bounds = [*dict.fromkeys(cuts[0]), cuts[1]]
        windows, factors, previous_factors = [], [], []
        for n, (low, high) in enumerate(zip(bounds, bounds[1:])):
            window = _score(conn, low, high, has_outcome)
            window["window"] = n + 1
            windows.append(window)
            if window["scored"]:
                previous_factors, factors = factors, _needed_factors(conn)
                if has_outcome:
                    window["reliability"] = _reliability(conn)
        scored = [w for w in windows if w["scored"]]
        flags = []
        if scored:
            latest = scored[-1]
            if latest["coverage_flag"]:
                flags.append({"flag": latest["coverage_flag"], "window": latest["window"]})
            first = scored[0]
            if len(scored) > 1 and latest["mean_absolute_error_seconds"] > ERROR_RISE * first["mean_absolute_error_seconds"]:
                flags.append({"flag": "error_rising", "window": latest["window"],
                              "ratio_to_first_window": round(latest["mean_absolute_error_seconds"] /
                                                             first["mean_absolute_error_seconds"], 6)})
        proposal = None
        if factors:
            finite = sorted(factors)
            factor = finite[min(len(finite) - 1, math.ceil(NOMINAL_COVERAGE * len(finite)) - 1)]
            if math.isfinite(factor):
                proposal = {"range_scaling_factor": round(factor, 6),
                            "latest_window_coverage_before": round(_coverage_at(factors, 1.0), 6),
                            "latest_window_coverage_after": round(_coverage_at(factors, factor), 6),
                            "previous_window_coverage_after": (round(_coverage_at(previous_factors, factor), 6)
                                                               if previous_factors else None),
                            "proposed_not_applied": True,
                            "meaning": ("Multiply each range's distance from the median by this factor: above 1 widens "
                                        "ranges that cover too little, below 1 narrows ranges that cover too much.")}
        return {
            "windows": windows, "flags": flags, "recalibration_proposal": proposal,
            "settings": settings.model_dump(),
            "method": {"split": "completed cases by end time into consecutive windows",
                       "training": "cases that ended before each window", "nominal_coverage": NOMINAL_COVERAGE,
                       "coverage_interval": "normal interval over cases, each case's share of covered prefixes",
                       "error_rise_threshold": ERROR_RISE},
            "interpretation": ("How past predictions would have performed on later cases in this log. A flag or "
                               "proposal is a prompt to review, not an automatic change, and does not establish "
                               "that future predictions hold."),
            "resources_used": False,
        }
    return workspace._query(log_id, settings.case_type, run)


__all__ = ["monitor", "parse"]
