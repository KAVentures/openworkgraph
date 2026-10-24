"""Predictive monitoring for open cases (roadmap #326 G1).

For each open case (its latest activity is not one of the activities that end a
case), estimate the remaining time and, optionally, an outcome risk from
completed cases that were in a comparable state: same latest activity and
same number of events so far. If too few completed cases match, fall back to
the same latest activity, then to all completed cases; the level used is
reported with every prediction.

How good the estimates are is measured, not assumed: a temporal backtest
learns from cases that finished before a cutoff and predicts the cases that
finished after it, reporting mean absolute error against a naive baseline,
how often the true remaining time fell inside the 10th–90th percentile range,
and the Brier score of outcome risks against the base rate.

Predictions are estimates from this log's history. They are not commitments,
targets or ratings of anyone, and no resources are used.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, ValidationError

from . import event_log_cohorts as cohorts
from . import event_log_workspace as workspace
from .event_log_analytics import _wilson

MAX_STEP = 10            # event counts above this share one comparison bucket
MAX_OPEN_CASES = 200
Label = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Outcome(_Strict):
    kind: Literal["duration_over", "contains_activity"]
    seconds: StrictInt | None = Field(default=None, ge=1, le=10 ** 9)
    activity: Label | None = None


class PredictionSpec(_Strict):
    end_activities: list[Label] = Field(min_length=1, max_length=20)
    outcome: Outcome | None = None
    min_support: StrictInt = Field(default=20, ge=5, le=10_000)
    backtest_share: StrictFloat | StrictInt = Field(default=0.3, gt=0, lt=1)
    cohort: dict[str, Any] = Field(default_factory=dict)
    case_type: Label | None = None
    limit: StrictInt = Field(default=50, ge=1, le=MAX_OPEN_CASES)


def parse(spec: Any) -> PredictionSpec:
    try:
        parsed = PredictionSpec.model_validate(spec)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise workspace.EventLogWorkspaceError(
            "prediction settings are invalid: " + ".".join(str(p) for p in first["loc"]) + " " + first["msg"]) from None
    cohorts.parse_cohort(parsed.cohort)
    outcome = parsed.outcome
    if outcome is not None and (outcome.kind == "duration_over") != (outcome.seconds is not None):
        raise workspace.EventLogWorkspaceError("duration_over needs seconds, and only it uses them")
    if outcome is not None and (outcome.kind == "contains_activity") != (outcome.activity is not None):
        raise workspace.EventLogWorkspaceError("contains_activity needs an activity, and only it uses one")
    return parsed


def _outcome_sql(outcome: Outcome | None) -> tuple[str, list[Any]]:
    if outcome is None:
        return "NULL::BOOLEAN", []
    if outcome.kind == "duration_over":
        return "epoch(c.ended - c.started) > ?", [outcome.seconds]
    return "list_contains(c.acts, ?)", [outcome.activity]


def predict(log_id: str, spec: Any) -> dict[str, Any]:
    settings = parse(spec)

    def run(conn):
        cohorts.restrict(conn, cohorts.parse_cohort(settings.cohort), log_id)
        as_of = conn.execute("SELECT max(ts) FROM ce").fetchone()[0]
        conn.execute("""CREATE OR REPLACE TEMP TABLE pc AS SELECT case_id, min(ts) AS started, max(ts) AS ended,
              count(*) AS events, arg_max(activity, struct_pack(t := ts, e := event_id)) AS last_activity,
              list(DISTINCT activity) AS acts FROM ce GROUP BY case_id""")
        conn.execute("ALTER TABLE pc ADD COLUMN complete BOOLEAN")
        conn.execute("UPDATE pc SET complete = list_contains(?, last_activity)", [settings.end_activities])
        outcome_sql, outcome_params = _outcome_sql(settings.outcome)
        # One training row per event of every completed case: the state after
        # that event and what happened next. The final event (nothing left)
        # is excluded so predictions are only about unfinished work.
        conn.execute(f"""CREATE OR REPLACE TEMP TABLE pp AS SELECT e.case_id, e.activity,
              least(e.n, {MAX_STEP}) AS step, epoch(c.ended - e.ts) AS remaining,
              epoch(e.ts - c.started) AS elapsed, c.ended, {outcome_sql} AS outcome
            FROM (SELECT case_id, activity, ts, row_number() OVER (PARTITION BY case_id ORDER BY ts, event_id) AS n
                  FROM ce) e JOIN pc c ON c.case_id = e.case_id
            WHERE c.complete AND e.n < c.events""", outcome_params)
        completed = conn.execute("SELECT count(*) FROM pc WHERE complete").fetchone()[0]
        open_total = conn.execute("SELECT count(*) FROM pc WHERE NOT complete").fetchone()[0]
        conn.execute("""CREATE OR REPLACE TEMP TABLE po AS SELECT case_id, last_activity AS activity,
              least(events, ?) AS step, epoch(? - started) AS age, epoch(? - ended) AS idle
            FROM pc WHERE NOT complete""", [MAX_STEP, as_of, as_of])
        _train(conn, "TRUE", [])
        _choose(conn, "po", settings.min_support)
        has_outcome = settings.outcome is not None
        rows = conn.execute("""SELECT case_id, activity, step, age, idle, basis, cases, q50, q10, q90, rate, hits, n
            FROM po_est WHERE basis IS NOT NULL ORDER BY """ + ("rate DESC" if has_outcome else "q50 DESC") +
                            ", case_id LIMIT ?", [settings.limit + 1]).fetchall()
        predicted = conn.execute("SELECT count(*) FROM po_est WHERE basis IS NOT NULL").fetchone()[0]
        predictions = []
        for case_id, activity, step, age, idle, basis, cases, q50, q10, q90, rate, hits, n in rows[:settings.limit]:
            item = {"case_id": case_id, "last_activity": activity, "events_so_far": step, "age_seconds": age,
                    "idle_seconds": idle, "basis": basis, "comparable_cases": cases, "median_remaining_seconds": q50,
                    "remaining_p10_seconds": q10, "remaining_p90_seconds": q90}
            if has_outcome:
                item["outcome_probability"] = rate
                item["outcome_probability_ci95"] = list(_wilson(hits, n, 1.959964))
            predictions.append(item)
        return {
            "as_of": as_of.isoformat() + "Z" if as_of else None,
            "completed_cases": completed, "open_cases": open_total,
            "predicted_open_cases": predicted, "unsupported_open_cases": open_total - predicted,
            "predictions": predictions, "predictions_truncated": predicted > len(predictions),
            "backtest": _backtest(conn, settings),
            "settings": settings.model_dump(),
            "basis": "historical completed cases in a comparable state (latest activity and step), with fallbacks",
            "interpretation": ("Estimates from this log's history, not commitments or targets. Remaining time is "
                               "measured to the last recorded event; no person is rated."),
            "resources_used": False,
        }
    return workspace._query(log_id, settings.case_type, run)


def _train(conn, where: str, params: list[Any]) -> None:
    """Remaining-time quantiles and outcome counts per comparable state, with fallback rows."""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE tr AS SELECT activity, step, grouping(activity, step) AS g,
          count(*) AS n, count(DISTINCT case_id) AS cases, count(*) FILTER (WHERE outcome) AS hits,
          quantile_cont(remaining, 0.1) AS q10, quantile_cont(remaining, 0.5) AS q50,
          quantile_cont(remaining, 0.9) AS q90
        FROM pp WHERE {where} GROUP BY GROUPING SETS ((activity, step), (activity), ())""", params)


def _choose(conn, source: str, support: int) -> None:
    """Attach the most specific state with enough distinct cases to every row of ``source``."""
    conn.execute(f"""CREATE OR REPLACE TEMP TABLE {source}_est AS SELECT t.*,
          CASE WHEN s1.cases >= ? THEN 'same_activity_and_step' WHEN s2.cases >= ? THEN 'same_activity'
               WHEN s0.cases >= ? THEN 'all_completed_cases' END AS basis,
          CASE WHEN s1.cases >= ? THEN s1.cases WHEN s2.cases >= ? THEN s2.cases WHEN s0.cases >= ? THEN s0.cases END AS cases,
          CASE WHEN s1.cases >= ? THEN s1.q50 WHEN s2.cases >= ? THEN s2.q50 WHEN s0.cases >= ? THEN s0.q50 END AS q50,
          CASE WHEN s1.cases >= ? THEN s1.q10 WHEN s2.cases >= ? THEN s2.q10 WHEN s0.cases >= ? THEN s0.q10 END AS q10,
          CASE WHEN s1.cases >= ? THEN s1.q90 WHEN s2.cases >= ? THEN s2.q90 WHEN s0.cases >= ? THEN s0.q90 END AS q90,
          CASE WHEN s1.cases >= ? THEN s1.hits WHEN s2.cases >= ? THEN s2.hits WHEN s0.cases >= ? THEN s0.hits END AS hits,
          CASE WHEN s1.cases >= ? THEN s1.n WHEN s2.cases >= ? THEN s2.n WHEN s0.cases >= ? THEN s0.n END AS n,
          s0.q50 AS naive_q50, s0.hits / s0.n AS base_rate
        FROM {source} t
        LEFT JOIN tr s1 ON s1.g = 0 AND s1.activity = t.activity AND s1.step = t.step
        LEFT JOIN tr s2 ON s2.g = 1 AND s2.activity = t.activity
        LEFT JOIN tr s0 ON s0.g = 3""", [support] * 21)
    conn.execute(f"ALTER TABLE {source}_est ADD COLUMN rate DOUBLE")
    conn.execute(f"UPDATE {source}_est SET rate = hits / n WHERE n > 0")


def _backtest(conn, settings: PredictionSpec) -> dict[str, Any]:
    """Learn from cases that ended before a cutoff; score predictions for cases that ended after it."""
    cutoff = conn.execute("SELECT quantile_disc(ended, ?) FROM pc WHERE complete",
                          [1 - float(settings.backtest_share)]).fetchone()[0]
    if cutoff is None:
        return {"available": False, "reason": "no completed cases"}
    _train(conn, "ended <= ?", [cutoff])
    conn.execute("CREATE OR REPLACE TEMP TABLE pt AS SELECT activity, step, remaining, outcome FROM pp WHERE ended > ?",
                 [cutoff])
    _choose(conn, "pt", settings.min_support)
    tests, scored, mae, naive, inside, brier, base_brier = conn.execute("""SELECT count(*),
          count(*) FILTER (WHERE basis IS NOT NULL),
          avg(abs(q50 - remaining)) FILTER (WHERE basis IS NOT NULL),
          avg(abs(naive_q50 - remaining)) FILTER (WHERE basis IS NOT NULL),
          avg(CASE WHEN remaining BETWEEN q10 AND q90 THEN 1.0 ELSE 0.0 END) FILTER (WHERE basis IS NOT NULL),
          avg(power(rate - CAST(outcome AS DOUBLE), 2)) FILTER (WHERE basis IS NOT NULL),
          avg(power(base_rate - CAST(outcome AS DOUBLE), 2)) FILTER (WHERE basis IS NOT NULL)
        FROM pt_est""").fetchone()
    if not scored:
        return {"available": False, "cutoff": cutoff.isoformat() + "Z", "test_prefixes": tests,
                "reason": "not enough completed cases before the cutoff to predict the cases after it"}
    out = {"available": True, "cutoff": cutoff.isoformat() + "Z", "test_prefixes": tests, "scored_prefixes": scored,
           "mean_absolute_error_seconds": mae, "naive_mean_absolute_error_seconds": naive,
           "improvement_over_naive": 1 - mae / naive if naive else None,
           "p10_p90_coverage": inside, "expected_p10_p90_coverage": 0.8,
           "naive_baseline": "median remaining time of all training prefixes"}
    if settings.outcome is not None:
        out.update(brier_score=brier, base_rate_brier_score=base_brier)
    return out


__all__ = ["parse", "predict"]
