"""Automation benefit ranges for an event log's automation candidates, under stated assumptions.

The candidate ranking (event_log_automation_candidates) deliberately estimates nothing. This module
turns it into hours-per-week and optional cost ranges only from assumptions the caller states:

* **measured**: occurrences per week of each activity (from the log);
* **assumed**: handling minutes per occurrence (a low-high range the caller gives; an event log
  records when steps happened, not how long anyone worked on them);
* **assumed**: the share of occurrences automation could take over, as a low-high range per routing
  pattern (fixed, mostly fixed, variable). Defaults are returned and can be replaced;
* **assumed, optional**: an hourly cost.

Every figure is a range, labelled measured or assumed. It is a planning aid for a person to review,
not a measured saving or a recommendation to automate.
"""
from __future__ import annotations

from typing import Any

from . import event_log_automation_candidates as candidates_module
from .event_log_workspace import EventLogWorkspaceError

FORMAT = "openworkgraph.event_log_automation_estimate.v1"
DEFAULT_AUTOMATABLE_SHARE = {"fixed": [0.6, 0.9], "mostly_fixed": [0.3, 0.6], "variable": [0.0, 0.3]}
PATTERN_ALTERNATIVE = {"fixed": "deterministic rules or scripted automation",
                       "mostly_fixed": "rules with a human handling exceptions",
                       "variable": "an AI agent with human approval, or keep it human"}


def _range(value: Any, what: str, low: float, high: float) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in value)):
        raise EventLogWorkspaceError(f"{what} must be a [low, high] pair of numbers")
    a, b = float(value[0]), float(value[1])
    if not low <= a <= b <= high:
        raise EventLogWorkspaceError(f"{what} must satisfy {low} <= low <= high <= {high}")
    return a, b


def estimate(log_id: str, case_type: str | None = None, *, handling_minutes: Any,
             automatable_share: Any = None, hourly_cost: Any = None, limit: int = 10,
             cohort: dict[str, Any] | None = None) -> dict[str, Any]:
    minutes = _range(handling_minutes, "handling_minutes", 0.0, 24 * 60)
    shares = dict(DEFAULT_AUTOMATABLE_SHARE)
    if automatable_share is not None:
        if not isinstance(automatable_share, dict) or set(automatable_share) - set(shares):
            raise EventLogWorkspaceError("automatable_share keys are fixed, mostly_fixed and variable")
        shares.update({k: list(_range(v, f"automatable_share.{k}", 0.0, 1.0)) for k, v in automatable_share.items()})
    cost = None
    if hourly_cost is not None:
        if isinstance(hourly_cost, bool) or not isinstance(hourly_cost, (int, float)) or not 0 < hourly_cost <= 100000:
            raise EventLogWorkspaceError("hourly_cost must be a positive number")
        cost = float(hourly_cost)
    ranked = candidates_module.candidates(log_id, case_type, limit=limit, cohort=cohort)
    rows, total = [], [0.0, 0.0]
    for item in ranked["candidates"]:
        lo_share, hi_share = shares[item["routing_pattern"]]
        hours = [round(item["per_week"] * minutes[0] / 60 * lo_share, 2),
                 round(item["per_week"] * minutes[1] / 60 * hi_share, 2)]
        total = [total[0] + hours[0], total[1] + hours[1]]
        rows.append({
            "activity": item["activity"], "routing_pattern": item["routing_pattern"],
            "alternative_to_consider": PATTERN_ALTERNATIVE[item["routing_pattern"]],
            "measured": {"occurrences_per_week": item["per_week"], "cases": item["cases"],
                         "median_wait_before_seconds": item["median_wait_before_seconds"]},
            "assumed": {"handling_minutes": list(minutes), "automatable_share": [lo_share, hi_share]},
            "hours_per_week": hours,
            **({"cost_per_year": [round(h * 52 * cost, 0) for h in hours]} if cost is not None else {}),
        })
    return {
        "format": FORMAT, "log": ranked.get("log"), "case_notion": ranked.get("case_notion"),
        "weeks_observed": ranked["weeks_observed"], "candidates": rows,
        "hours_per_week_total": [round(total[0], 2), round(total[1], 2)],
        **({"cost_per_year_total": [round(total[0] * 52 * cost, 0), round(total[1] * 52 * cost, 0)]}
           if cost is not None else {}),
        "assumptions": {"handling_minutes": list(minutes), "automatable_share": shares,
                        **({"hourly_cost": cost} if cost is not None else {}),
                        "automatable_share_defaults": DEFAULT_AUTOMATABLE_SHARE},
        "interpretation": ("Ranges from stated assumptions, not measured savings. Only occurrences per week come "
                           "from the log. Waiting time is not counted as effort. Review each candidate with the "
                           "people who do the work before deciding anything."),
    }


__all__ = ["estimate", "FORMAT", "DEFAULT_AUTOMATABLE_SHARE"]
