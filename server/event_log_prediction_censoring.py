"""Coverage of still-open cases excluded from completed-case monitoring (#370 R19).

This is a descriptive diagnostic over the selected cohort's temporary case
and completed-case-prefix tables. It has no identifiers, source refresh,
training side effects, or permission to change a prediction.
"""
from __future__ import annotations

from typing import Any

from . import event_log_predictions as predictions

MAX_DISPLAY_STATES = 8


def summarize(conn, *, min_support: int) -> dict[str, Any]:
    """Count exact-state analogue coverage without treating open cases as labels.

    Must be called after predictions._prepare has built temporary `pc` and
    `pp`. An open case contributes once in its latest observed activity/step;
    completed cases contribute prefix examples. This DOES NOT establish
    predictive accuracy or correct for censoring.
    """
    complete, open_count = conn.execute(
        "SELECT count(*) FILTER (WHERE complete), count(*) FILTER (WHERE NOT complete) FROM pc"
    ).fetchone()
    completed = int(complete)
    unfinished = int(open_count)
    rows = conn.execute(
        """WITH open_states AS (
              SELECT last_activity AS activity, least(events, ?) AS step,
                     count(*) AS open_cases
              FROM pc WHERE NOT complete GROUP BY 1,2
           ), completed_states AS (
              SELECT activity,step,count(DISTINCT case_id) AS completed_prefix_cases
              FROM pp GROUP BY 1,2
           )
           SELECT o.activity,o.step,o.open_cases,coalesce(c.completed_prefix_cases,0)
           FROM open_states o LEFT JOIN completed_states c
             ON o.activity=c.activity AND o.step=c.step
           ORDER BY o.open_cases DESC,o.activity ASC,o.step ASC""",
        [predictions.MAX_STEP],
    ).fetchall()
    exact = sum(int(count) for _, _, count, support in rows if int(support) >= min_support)
    unseen = sum(int(count) for _, _, count, support in rows if int(support) == 0)
    weak = unfinished - exact
    displayed = [
        {"latest_activity": str(activity)[:200], "step": int(step),
         "open_cases": int(count), "exact_completed_prefix_cases": int(support),
         "exact_state_supported": int(support) >= min_support}
        for activity, step, count, support in rows[:MAX_DISPLAY_STATES]
    ]
    return {
        "status": ("no_open_cases" if unfinished == 0 else
                   "exact_state_fallback_needed" if weak else "all_open_states_supported"),
        "completed_cases_in_selected_cohort": completed,
        "still_open_cases_not_scored": unfinished,
        "selected_cases_total": completed + unfinished,
        "still_open_share": round(unfinished / (completed + unfinished), 6)
                            if completed + unfinished else 0.0,
        "open_cases_with_exact_state_support": exact,
        "open_cases_without_exact_state_support": weak,
        "open_cases_with_no_exact_completed_prefix": unseen,
        "minimum_completed_examples_for_exact_state": min_support,
        "distinct_open_states": len(rows),
        "displayed_states": len(displayed),
        "states_truncated": len(rows) > len(displayed),
        "open_states": displayed,
        "selection_basis": "selected cohort; last observed activity and capped step",
        "training_basis": "completed-case prefixes in the selected cohort, not outcomes of open cases",
        "open_cases_contributed_outcome_labels": False,
        "censoring_adjustment_applied": False,
        "causal_or_future_accuracy_claim": False,
        "interpretation": ("Completed-case monitoring excludes cases that have not reached a selected end "
                           "activity. Exact-state support indicates only how many completed training prefixes "
                           "resemble each currently open state. It does not validate predictions for those "
                           "open cases, correct censoring, or establish future calibration."),
    }


__all__ = ["MAX_DISPLAY_STATES", "summarize"]
