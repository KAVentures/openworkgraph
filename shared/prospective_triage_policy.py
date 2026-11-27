"""Deterministic capacity-matched prospective triage policy arithmetic.

No database, model fitting, outcome-based ranking, or intervention execution.
All uncertain-label bounds are sharp for the frozen eligible population,
conditional on each reported observation and unrestricted unknown binary labels.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FORMAT = "openworkgraph.prospective-triage-policy.v1"
MAX_CASES = 5000
MAX_ACTIVITY_GROUPS = 30
MAX_COST = 1_000_000


class PolicyError(ValueError):
    pass


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    label: str = Field(min_length=1, max_length=80)
    target: Literal["frozen_outcome", "remaining_time_over"]
    remaining_seconds: int | None = Field(default=None, ge=1, le=10**9)
    capacity: int = Field(ge=1, le=MAX_CASES)
    tie_seed: int = Field(ge=0, le=2**31-1)
    false_positive_cost: int = Field(ge=0, le=MAX_COST)
    false_negative_cost: int = Field(ge=0, le=MAX_COST)
    minimum_group_cases: int = Field(default=10, ge=5, le=500)


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                            separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def parse(value):
    try:
        spec = Spec.model_validate(value)
    except ValidationError:
        raise PolicyError("invalid bounded triage policy settings") from None
    if not spec.label.strip() or not spec.label.isprintable():
        raise PolicyError("use a printable nonempty policy label")
    if (spec.target == "remaining_time_over") != (spec.remaining_seconds is not None):
        raise PolicyError("remaining-time target requires its own positive seconds threshold")
    if not spec.false_positive_cost and not spec.false_negative_cost:
        raise PolicyError("declare at least one nonzero error cost")
    return spec


def _number(value, probability=False):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0
            and (value <= 1 if probability else value <= 10**12))


def plan(cases, spec, *, frozen_outcome_available):
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES:
        raise PolicyError("the snapshot must contain 1 to 5000 supported predictions")
    if spec.target == "frozen_outcome" and not frozen_outcome_available:
        raise PolicyError("this snapshot has no frozen outcome model")
    risk = spec.target == "frozen_outcome"
    model_key = "outcome_probability" if risk else "median_remaining_seconds"
    baseline_key = "base_rate" if risk else "naive_median_remaining_seconds"
    eligible, exclusions, seen = [], [], set()
    for item in cases:
        if not isinstance(item, dict):
            raise PolicyError("malformed frozen prediction")
        key = item.get("case_id")
        if not isinstance(key, str) or not 1 <= len(key) <= 1000 or key in seen:
            raise PolicyError("invalid or duplicate frozen case identity")
        seen.add(key)
        model, baseline = item.get(model_key), item.get(baseline_key)
        if not (_number(model, risk) and _number(baseline, risk)):
            exclusions.append({"case_id": key, "reason": "missing_or_invalid_paired_estimate"})
            continue
        activity, basis = item.get("last_activity"), item.get("basis")
        if not isinstance(activity, str) or not isinstance(basis, str):
            raise PolicyError("malformed frozen prediction stratum")
        eligible.append({"case_id": key, "model_score": model, "baseline_score": baseline,
                         "activity": activity, "basis": basis})
    if not eligible:
        raise PolicyError("no frozen cases have valid paired estimates")
    # Same deterministic, outcome-independent tie breaker in both arms.
    # No preference for case-ID lexical order except the negligible hash tie.
    def tie(item):
        return digest([FORMAT, spec.tie_seed, item["case_id"]]), item["case_id"]
    k = min(spec.capacity, len(eligible))
    model = sorted(eligible, key=lambda x: (-x["model_score"], *tie(x)))
    baseline = sorted(eligible, key=lambda x: (-x["baseline_score"], *tie(x)))
    m_rank = {r["case_id"]: i + 1 for i, r in enumerate(model)}
    b_rank = {r["case_id"]: i + 1 for i, r in enumerate(baseline)}
    rows = []
    for row in sorted(eligible, key=lambda x: x["case_id"]):
        key = row["case_id"]
        mr, br = m_rank[key], b_rank[key]
        rows.append({**row, "model_rank": mr, "baseline_rank": br,
                     "model_selected": mr <= k, "baseline_selected": br <= k})
    counts = Counter(assignment(row) for row in rows)
    return {"rows": rows, "excluded": sorted(exclusions, key=lambda x: x["case_id"]),
            "population": len(cases), "eligible": len(rows),
            "effective_capacity": k, "requested_capacity": spec.capacity,
            "assignment_counts": {key: counts[key] for key in ("both", "model_only", "baseline_only", "neither")},
            "model_cutoff_score": model[k-1]["model_score"],
            "baseline_cutoff_score": baseline[k-1]["baseline_score"],
            "model_cutoff_ties": sum(r["model_score"] == model[k-1]["model_score"] for r in model),
            "baseline_cutoff_ties": sum(r["baseline_score"] == baseline[k-1]["baseline_score"] for r in baseline),
            "ranking_sha256": digest(rows)}


def assignment(row):
    return ("both" if row["baseline_selected"] else "model_only") if row["model_selected"] else (
        "baseline_only" if row["baseline_selected"] else "neither")


def observation(row, current, horizon_valid, since, spec):
    """Labels reflect only recorded future evidence, not actual intervention."""
    item = current.get(row["case_id"])
    if item is None:
        return "missing", None
    complete, remaining, outcome = item
    if not horizon_valid or (complete and (not _number(remaining))):
        return "inconsistent", None
    if complete:
        if spec.target == "remaining_time_over":
            return "finished", int(remaining > spec.remaining_seconds)
        return ("finished", int(outcome)) if type(outcome) is bool else ("finished_label_unavailable", None)
    if spec.target == "remaining_time_over" and since > spec.remaining_seconds:
        return "open_known_late", 1
    return "still_open", None


def _ratio(num, den):
    return round(num / den, 8) if den else None


def _cost(selected, label, spec):
    return (spec.false_positive_cost if selected and label == 0 else
            spec.false_negative_cost if not selected and label == 1 else 0)


def _arm(rows, field, spec):
    counts = Counter((bool(r[field]), r["label"]) for r in rows)
    tp, fp, fn, tn = counts[True, 1], counts[True, 0], counts[False, 1], counts[False, 0]
    us, un = counts[True, None], counts[False, None]
    selected = tp+fp+us
    # For recall, unknown selected/unselected labels can vary independently.
    # Undefined denominators remain null, not zero or perfect performance.
    recall_lower = _ratio(tp, tp+fn+un)
    recall_upper = _ratio(tp+us, tp+us+fn)
    cost_known = fp*spec.false_positive_cost + fn*spec.false_negative_cost
    return {"selected": selected, "not_selected": len(rows)-selected,
            "known_confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
            "unknown_selected": us, "unknown_not_selected": un,
            "eventual_confusion_bounds": {"tp": [tp,tp+us], "fp": [fp,fp+us],
                                          "fn": [fn,fn+un], "tn": [tn,tn+un]},
            "eventual_precision_bounds": [_ratio(tp,selected), _ratio(tp+us,selected)],
            "eventual_recall_bounds_when_defined": [recall_lower, recall_upper],
            "recall_undefined_possible": tp+fn == 0,
            "eventual_error_cost_bounds": [cost_known,
                cost_known+us*spec.false_positive_cost+un*spec.false_negative_cost],
            "known_error_cost": cost_known}


def _comparison(rows, spec):
    capture_lo = capture_hi = cost_lo = cost_hi = 0
    known_delta, unknown_disagreements = 0, 0
    for row in rows:
        a, b = row["model_selected"], row["baseline_selected"]
        label = row["label"]
        values = (0,1) if label is None else (label,)
        capture = [(int(a)-int(b))*y for y in values]
        costs = [_cost(a,y,spec)-_cost(b,y,spec) for y in values]
        capture_lo += min(capture); capture_hi += max(capture)
        cost_lo += min(costs); cost_hi += max(costs)
        if label is not None:
            known_delta += capture[0]
        if label is None and a != b:
            unknown_disagreements += 1
    return {"captured_positive_change_bounds": [capture_lo,capture_hi],
            "model_minus_baseline_error_cost_bounds": [cost_lo,cost_hi],
            "known_captured_positive_change": known_delta,
            "unknown_disagreement_cases": unknown_disagreements,
            "capture_direction": ("model_strictly_more" if capture_lo>0 else
                                  "baseline_strictly_more" if capture_hi<0 else
                                  "equal" if capture_lo==capture_hi==0 else "unresolved_or_tie"),
            "cost_direction": ("model_strictly_lower" if cost_hi<0 else
                               "baseline_strictly_lower" if cost_lo>0 else
                               "equal" if cost_lo==cost_hi==0 else "unresolved_or_tie")}


def summarize(rows, spec):
    observations = Counter(r["observation"] for r in rows)
    assignments = Counter(assignment(r) for r in rows)
    return {"eligible_cases": len(rows), "known_labels": sum(r["label"] is not None for r in rows),
            "known_positive_labels": sum(r["label"] == 1 for r in rows),
            "unknown_labels": sum(r["label"] is None for r in rows),
            "observation_counts": dict(sorted(observations.items())),
            "assignment_counts": {k:assignments[k] for k in ("both","model_only","baseline_only","neither")},
            "model": _arm(rows,"model_selected",spec),
            "baseline": _arm(rows,"baseline_selected",spec),
            "paired": _comparison(rows,spec)}


def strata(rows, spec, field, limit):
    grouped = defaultdict(list)
    for row in rows:
        grouped[row[field]].append(row)
    ordered = sorted(grouped.items(), key=lambda x: (-len(x[1]),x[0]))
    displayed = []
    withheld, withheld_cases = 0, 0
    for name, group in ordered:
        if len(group) < spec.minimum_group_cases or len(displayed) >= limit:
            withheld += 1; withheld_cases += len(group)
            continue
        displayed.append({"group":name, **summarize(group,spec)})
    return {"groups":displayed,"total_groups":len(grouped),"withheld_groups":withheld,
            "withheld_cases":withheld_cases,"minimum_group_cases":spec.minimum_group_cases,
            "maximum_displayed_groups":limit}


def evaluate(plan_result, current, *, horizon_valid, since, spec):
    rows = []
    for row in plan_result["rows"]:
        status, label = observation(row,current,horizon_valid,since,spec)
        rows.append({**row,"observation":status,"label":label,"assignment":assignment(row)})
    return {"summary":summarize(rows,spec),
            "by_frozen_activity":strata(rows,spec,"activity",MAX_ACTIVITY_GROUPS),
            "by_frozen_model_basis":strata(rows,spec,"basis",MAX_ACTIVITY_GROUPS),
            "rows":rows}
