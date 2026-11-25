"""Descriptive decomposition and measurement-period sensitivity (R16).

Inputs are already normalized, nonoverlapping positive-denominator periods.
This module does not estimate treatment effects or statistical uncertainty.
"""
from datetime import date


def _exposure(rows, month_days):
    days = sum(row["days"] for row in rows)
    cases = sum(row["cases"] for row in rows)
    hours = sum(row["_hours"] for row in rows)
    return hours * 60 / cases, cases * month_days / days


def _change(baseline, after, month_days):
    before_effort, before_volume = _exposure(baseline, month_days)
    after_effort, after_volume = _exposure(after, month_days)
    return (before_effort - after_effort) * after_volume / 60


def _calendar(rows):
    span = (max(date.fromisoformat(r["end"]) for r in rows)
            - min(date.fromisoformat(r["start"]) for r in rows)).days + 1
    measured = sum(r["days"] for r in rows)
    sources = {}
    for row in rows:
        item = sources.setdefault(row["source"], {"periods": 0, "cases": 0, "days": 0})
        item["periods"] += 1
        item["cases"] += row["cases"]
        item["days"] += row["days"]
    return {"span_days": span, "measured_days": measured,
            "unmeasured_days_inside_span": span - measured,
            "calendar_coverage_fraction": round(measured / span, 4),
            "source_kind_exposure": dict(sorted(sources.items()))}


def review_sensitivity(baseline, after, month_days):
    """Separate effort and volume arithmetically and omit each period in turn."""
    b_effort, b_volume = _exposure(baseline, month_days)
    a_effort, a_volume = _exposure(after, month_days)
    # Symmetric (two-order average) decomposition of b_e*b_v - a_e*a_v.
    effort = (b_effort - a_effort) * (b_volume + a_volume) / 120
    volume = (b_volume - a_volume) * (b_effort + a_effort) / 120
    original = (b_effort - a_effort) * a_volume / 60
    alternatives = []
    for kind, rows in (("baseline", baseline), ("after", after)):
        if len(rows) < 2:
            continue
        for index, omitted in enumerate(rows):
            remaining = rows[:index] + rows[index+1:]
            change = _change(remaining if kind == "baseline" else baseline,
                             remaining if kind == "after" else after, month_days)
            alternatives.append({"omitted_period": kind, "measurement_id": omitted["measurement_id"],
                                 "effort_change_at_after_volume_hours_per_month": round(change, 3),
                                 "direction": "lower_effort" if change > 0 else "higher_effort" if change < 0 else "no_change"})
    values = [row["effort_change_at_after_volume_hours_per_month"] for row in alternatives]
    directions = {row["direction"] for row in alternatives}
    baseline_source_kinds = {r["source"] for r in baseline}
    after_source_kinds = {r["source"] for r in after}
    return {
        "format": "openworkgraph.reported-benefit-sensitivity.v1",
        "monthly_hours_change_decomposition": {
            "method": "symmetric_average_of_two_arithmetic_orders",
            "reported_baseline_minus_after_hours": round(effort + volume, 3),
            "effort_per_case_component_hours": round(effort, 3),
            "case_volume_component_hours": round(volume, 3),
            "components_may_differ_from_total_due_to_rounding": True,
        },
        "calendar_coverage": {"baseline": _calendar(baseline), "after": _calendar(after)},
        "declared_source_kind_sets_match": baseline_source_kinds == after_source_kinds,
        "leave_one_period_out": {
            "original_effort_change_hours_per_month": round(original, 3),
            "baseline_period_omission_supported": len(baseline) >= 2,
            "after_period_omission_supported": len(after) >= 2,
            "scenarios": alternatives,
            "scenario_count": len(alternatives),
            "scenario_range_hours_per_month": [min(values), max(values)] if values else None,
            "direction_changes_across_scenarios": len(directions) > 1 if values else None,
            "direction_differs_from_full_comparison": any(
                direction != ("lower_effort" if original > 0 else "higher_effort" if original < 0 else "no_change")
                for direction in directions) if values else None,
        },
        "sources_independently_verified": False,
        "causal_effect_verified": False,
        "statistical_confidence_interval": False,
        "realized_savings_verified": False,
    }
