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
from . import event_log_prediction_censoring as censoring
from .event_log_analytics import _wilson
from . import event_log_workspace as workspace

NOMINAL_COVERAGE = 0.8
COVERAGE_FLOOR = 0.05          # a flag needs this gap from nominal, not only an interval that excludes it
ERROR_RISE = 1.25
MIN_CASES_PER_WINDOW = 10
MIN_CASES_PER_ACTIVITY = 20  # A displayed activity needs 20 independent completed cases.
MAX_ACTIVITY_ROWS = 8


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


MIN_MATERIAL_BRIER_EXCESS = 0.02  # paired, absolute squared-probability-loss difference


def _paired_outcome_quality(conn) -> dict[str, Any] | None:
    """Compare the risk model to the *same* base-rate labels, treating each case as one unit.

    A single completed case can contribute several correlated prefixes. Taking
    an ordinary standard error across all prefixes creates false certainty.
    Average the paired squared-loss difference within each case first, then
    form a descriptive normal interval across cases. No update is applied.
    """
    values = [float(row[0]) for row in conn.execute("""
        SELECT avg(power(rate - CAST(outcome AS DOUBLE), 2) -
                   power(base_rate - CAST(outcome AS DOUBLE), 2)) AS paired_excess
        FROM pt_est
        WHERE basis IS NOT NULL AND rate IS NOT NULL
          AND base_rate IS NOT NULL AND outcome IS NOT NULL
        GROUP BY case_id ORDER BY case_id
    """).fetchall() if row[0] is not None]
    count = len(values)
    if count < MIN_CASES_PER_WINDOW:
        return None
    excess = sum(values) / count
    sd = math.sqrt(sum((v - excess) ** 2 for v in values) / (count - 1)) if count > 1 else 0.0
    margin = 1.959964 * sd / math.sqrt(count)
    interval = [max(-1.0, excess - margin), min(1.0, excess + margin)]
    # A warning requires *both* a nonzero lower 95% bound and a material
    # absolute excess. Negative excess means the model is better than baseline.
    warn = excess >= MIN_MATERIAL_BRIER_EXCESS and interval[0] > 0
    return {
        "case_averaged_brier_excess": round(excess, 6),
        "case_averaged_brier_excess_ci95": [round(x, 6) for x in interval],
        "paired_cases": count,
        "material_excess_threshold": MIN_MATERIAL_BRIER_EXCESS,
        "worse_than_base_rate": warn,
        "positive_excess_means": "the model incurs more squared probability error than the historical base rate",
        "unit_of_uncertainty": "completed_case_not_correlated_prefix",
        "review_only": True,
    }


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
        out.update(brier_score=brier, base_rate_brier_score=base_brier,
                   brier_skill_vs_base_rate=(round(1 - brier / base_brier, 6)
                                             if brier is not None and base_brier else None),
                   outcome_quality=_paired_outcome_quality(conn))
    return out



def _activity_quality(conn, has_outcome: bool) -> dict[str, Any]:
    """Review-only latest-window diagnostics by observed *activity state*.

    An activity's repeated prefixes within the same completed case are averaged
    first. No case names, resource identities or example rows leave this query.
    Suppress groups with fewer than 20 distinct cases; cap the visible groups.
    A case can contribute to multiple activity states, so group totals cannot
    be added to infer independent case counts.
    """
    counts = conn.execute("""
        SELECT count(*), count(*) FILTER (WHERE cases < ?)
        FROM (SELECT activity, count(DISTINCT case_id) AS cases
              FROM pt_est WHERE basis IS NOT NULL AND activity IS NOT NULL
              GROUP BY activity) states
    """, [MIN_CASES_PER_ACTIVITY]).fetchone()
    rows = conn.execute("""
        SELECT activity, count(*) AS cases, sum(prefixes) AS prefixes,
               avg(covered) AS coverage, stddev_samp(covered) AS coverage_sd,
               avg(abs_error) AS mae, avg(naive_error) AS naive_mae,
               avg(outcome_loss) AS outcome_loss, avg(base_loss) AS base_loss
        FROM (
            SELECT activity, case_id, count(*) AS prefixes,
                   avg(CASE WHEN remaining BETWEEN q10 AND q90 THEN 1.0 ELSE 0.0 END) AS covered,
                   avg(abs(q50 - remaining)) AS abs_error,
                   avg(abs(naive_q50 - remaining)) AS naive_error,
                   avg(power(rate - CAST(outcome AS DOUBLE), 2)) AS outcome_loss,
                   avg(power(base_rate - CAST(outcome AS DOUBLE), 2)) AS base_loss
            FROM pt_est WHERE basis IS NOT NULL AND activity IS NOT NULL
            GROUP BY activity, case_id
        ) case_states
        GROUP BY activity HAVING count(*) >= ?
        ORDER BY count(*) DESC, activity ASC LIMIT ?
    """, [MIN_CASES_PER_ACTIVITY, MAX_ACTIVITY_ROWS]).fetchall()
    result = []
    for activity, cases, prefixes, coverage, coverage_sd, mae, baseline, brier, base_brier in rows:
        margin = 1.959964 * (coverage_sd or 0.0) / math.sqrt(cases)
        item = {
            "activity": activity, "scored_cases": cases, "scored_prefixes": prefixes,
            "mean_absolute_error_seconds": round(mae, 6),
            "naive_mean_absolute_error_seconds": round(baseline, 6),
            "p10_p90_case_averaged_coverage": round(coverage, 6),
            "p10_p90_coverage_ci95": [
                round(max(0.0, coverage - margin), 6),
                round(min(1.0, coverage + margin), 6),
            ],
        }
        if has_outcome:
            item["case_averaged_brier_score"] = round(brier, 6) if brier is not None else None
            item["case_averaged_base_rate_brier_score"] = (
                round(base_brier, 6) if base_brier is not None else None)
        result.append(item)
    total_states, small_states = (int(counts[0] or 0), int(counts[1] or 0))
    return {
        "activities": result,
        "total_scored_activity_states": total_states,
        "withheld_low_support_states": small_states,
        "withheld_over_limit_states": max(0, total_states - small_states - len(result)),
        "minimum_distinct_completed_cases": MIN_CASES_PER_ACTIVITY,
        "maximum_displayed_states": MAX_ACTIVITY_ROWS,
        "unit_of_uncertainty": "completed_case_within_activity_not_correlated_prefix",
        "no_case_or_resource_identifiers": True,
        "interpretation": ("Descriptive latest-scored-window errors for the activity at each "
                           "prediction point; not a ranking of employees or proof of causes. "
                           "A completed case may contribute to multiple activity states. "
                           "Intervals are descriptive and not corrected for multiple comparisons."),
    }


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


MIN_CASES_PER_RISK_BIN = 20


def _case_outcome_reliability(conn) -> dict[str, Any]:
    """Count completed cases once, rather than treating prefixes as independent."""
    # A case may have dozens of scored prefixes with the same eventual outcome.
    # Mean predictions within that case before assigning one risk bin per case.
    rows = conn.execute("""
        WITH cases AS (
            SELECT case_id, avg(CAST(rate AS DOUBLE)) AS predicted,
                   max(CAST(outcome AS INTEGER)) AS actual,
                   count(*) AS prefixes
            FROM pt_est
            WHERE basis IS NOT NULL AND rate IS NOT NULL AND outcome IS NOT NULL
            GROUP BY case_id HAVING count(DISTINCT outcome) = 1
        )
        SELECT least(4, CAST(floor(predicted * 5) AS INTEGER)) AS band,
               count(*) AS cases, sum(prefixes) AS prefixes,
               avg(predicted) AS mean_predicted, sum(actual) AS outcomes,
               avg(power(predicted - actual, 2)) AS case_brier
        FROM cases GROUP BY 1 ORDER BY 1
    """).fetchall()
    bins, suppressed = [], 0
    for band, cases, prefixes, mean_predicted, positives, case_brier in rows:
        n, happened = int(cases), int(positives)
        if n < MIN_CASES_PER_RISK_BIN:
            suppressed += 1
            continue
        lower, upper = _wilson(happened, n, 1.959964)
        bins.append({
            "predicted_from": band / 5,
            "predicted_to_exclusive": (band + 1) / 5 if band < 4 else None,
            "predicted_to_inclusive": 1.0 if band == 4 else None,
            "completed_cases": n,
            "scored_prediction_points": int(prefixes),
            "mean_case_averaged_predicted_risk": round(mean_predicted, 6),
            "outcome_cases": happened,
            "observed_outcome_share": round(happened / n, 6),
            "observed_outcome_ci95": [round(lower, 6), round(upper, 6)],
            "case_averaged_brier_score": round(case_brier, 6),
        })
    return {
        "bins": bins,
        "total_distinct_scored_cases": sum(int(row[1]) for row in rows),
        "total_scored_prediction_points": sum(int(row[2]) for row in rows),
        "minimum_distinct_completed_cases_per_displayed_bin": MIN_CASES_PER_RISK_BIN,
        "withheld_low_support_bins": suppressed,
        "total_nonempty_bins": len(rows),
        "status": "ready" if bins else "insufficient_independent_case_support",
        "case_identity_or_resource_labels_included": False,
        "source_outcomes_independently_verified": False,
        "recalibration_applied": False,
        "unit_of_uncertainty": "independent_completed_case_not_correlated_prefix",
        "interpretation": ("One completed case contributes once, using the average of its scored "
                           "probabilities. The Wilson interval covers the observed outcome share, "
                           "not an intervention effect or independent verification of the label."),
    }


def monitor(log_id: str, spec: Any) -> dict[str, Any]:
    settings = parse(spec)
    has_outcome = settings.outcome is not None

    def run(conn):
        predictions._prepare(conn, settings, log_id)
        open_case_coverage = censoring.summarize(conn, min_support=settings.min_support)
        conn.execute("SET VARIABLE owg_support = ?", [settings.min_support])
        quantiles = [k / (settings.windows + 1) for k in range(1, settings.windows + 1)]
        cuts = conn.execute("SELECT quantile_disc(ended, ?), max(ended) FROM pc WHERE complete",
                            [quantiles]).fetchone()
        if cuts[0] is None:
            raise workspace.EventLogWorkspaceError("monitoring needs completed cases; check the end activities")
        bounds = [*dict.fromkeys(cuts[0]), cuts[1]]
        windows, factors, previous_factors = [], [], []
        activity_quality = None
        outcome_case_reliability = None
        for n, (low, high) in enumerate(zip(bounds, bounds[1:])):
            window = _score(conn, low, high, has_outcome)
            window["window"] = n + 1
            windows.append(window)
            if window["scored"]:
                previous_factors, factors = factors, _needed_factors(conn)
                activity_quality = _activity_quality(conn, has_outcome)
                activity_quality["window"] = window["window"]
                if has_outcome:
                    window["reliability"] = _reliability(conn)  # Legacy, prefix-weighted.
                    outcome_case_reliability = _case_outcome_reliability(conn)
                    outcome_case_reliability["window"] = window["window"]
        scored = [w for w in windows if w["scored"]]
        flags = []
        if scored:
            latest = scored[-1]
            if latest["coverage_flag"]:
                flags.append({"flag": latest["coverage_flag"], "window": latest["window"]})
            if has_outcome and latest.get("outcome_quality") and latest["outcome_quality"]["worse_than_base_rate"]:
                flags.append({
                    "flag": "outcome risk model performs worse than historical base rate",
                    "window": latest["window"],
                    "case_averaged_brier_excess": latest["outcome_quality"]["case_averaged_brier_excess"],
                    "case_averaged_brier_excess_ci95": latest["outcome_quality"]["case_averaged_brier_excess_ci95"],
                    "comparison": "paired_loss_by_case",
                    "review_only": True,
                })
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
            "activity_quality": activity_quality,
            "outcome_case_reliability": outcome_case_reliability,
            "open_case_coverage": open_case_coverage,
            "settings": settings.model_dump(),
            "method": {"split": "completed cases by end time into consecutive windows",
                       "training": "cases that ended before each window", "nominal_coverage": NOMINAL_COVERAGE,
                       "open_case_coverage": "descriptive census of still-open cases excluded from completed-case scoring",
                       "coverage_interval": "normal interval over cases, each case's share of covered prefixes",
                       "error_rise_threshold": ERROR_RISE,
                       "outcome_quality_method": "paired Brier excess averaged within case then across cases",
                       "outcome_quality_minimum_material_excess": MIN_MATERIAL_BRIER_EXCESS,
                       "outcome_quality_warning_window": "latest_scored_only",
                       "case_outcome_reliability_min_cases_per_bin": MIN_CASES_PER_RISK_BIN,
                       "case_outcome_reliability_risk_basis": "mean_scored_probability_within_each_completed_case"},
            "interpretation": ("How past predictions would have performed on later cases in this log. A flag or "
                               "proposal is a prompt to review, not an automatic change, and does not establish "
                               "that future predictions hold."),
            "resources_used": False,
        }
    return workspace._query(log_id, settings.case_type, run)


__all__ = ["monitor", "parse"]
