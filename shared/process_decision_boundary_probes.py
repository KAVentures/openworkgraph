"""Finite synthetic boundary probes for reviewed numeric decision tables (#370 R21).

This supplements caller-supplied case tests. It never reads or retains source
facts, proves full domain coverage, changes a model, or authorizes execution.
Unsupported predicate shapes and decision dependencies are refused explicitly.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from .process_decision_tables import evaluate_table, validate_date
from .process_rule_conditions import validate_facts

FORMAT = "openworkgraph.decision-table-boundary-probes.v1"
NUMERIC_OPS = frozenset(("gt", "gte", "lt", "lte"))
MAX_PROBES = 83  # 40 distinct thresholds, 41 intervals and two domain endpoints
MAX_WITNESSES = 4
LIMIT = Decimal(10**15)


def _supported(table, rules):
    if not table.user_confirmed or any(not rules[row.rule_id].user_confirmed for row in table.rows):
        return None, "unconfirmed"
    if table.required_decisions:
        return None, "decision_dependencies_need_real_case_context"

    path = None
    thresholds = set()
    for row in table.rows:
        predicate = rules[row.rule_id].predicate
        if predicate is None or len(predicate.conditions) != 1:
            return None, "requires_one_numeric_comparison_per_row"
        clause = predicate.conditions[0]
        if predicate.match not in ("all", "any") or clause.operator not in NUMERIC_OPS:
            return None, "requires_one_numeric_comparison_per_row"
        if type(clause.expected) not in (int, float):
            return None, "requires_one_numeric_comparison_per_row"
        if path is not None and clause.path != path:
            return None, "requires_same_numeric_fact_path"
        path = clause.path
        thresholds.add(Decimal(str(clause.expected)))
    if not path:
        return None, "no_numeric_path"
    segments = [s.replace("~1", "/").replace("~0", "~") for s in path.split("/")[1:]]
    # A synthetic JSON object is sufficient for simple field paths but is not
    # a faithful substitute for JSON pointer array indexing or unsafe keys.
    if any(not s or len(s) > 120 or s.isdigit() for s in segments):
        return None, "numeric_path_has_unsupported_shape"
    return (path, segments, sorted(thresholds)), None


def _representable(candidate: Decimal, low: Decimal | None = None,
                   high: Decimal | None = None) -> int | float | None:
    """Return a validated JSON number inside the requested open interval."""
    if not -LIMIT <= candidate <= LIMIT:
        return None
    try:
        number: int | float = int(candidate) if candidate == candidate.to_integral_value() else float(candidate)
        if not -(10**15) <= number <= 10**15:
            return None
        restored = Decimal(str(number))
        if restored != candidate and low is None and high is None:
            # A boundary must retain its declared decimal value exactly.
            return None
        if low is not None and not restored > low:
            return None
        if high is not None and not restored < high:
            return None
        return number
    except (OverflowError, ValueError):
        return None


def _candidates(thresholds: list[Decimal]) -> tuple[list[int | float], int]:
    """Include thresholds, each open interval, and both numeric domain ends."""
    result: dict[Decimal, int | float] = {}
    unprobed = 0

    def add(number):
        if number is not None:
            result[Decimal(str(number))] = number

    add(int(-LIMIT))
    add(int(LIMIT))
    for value in thresholds:
        number = _representable(value)
        if number is None:
            unprobed += 1
        else:
            add(number)
    boundaries = [-LIMIT, *[t for t in thresholds if -LIMIT < t < LIMIT], LIMIT]
    for low, high in zip(boundaries, boundaries[1:]):
        mid = (low + high) / 2
        sample = _representable(mid, low, high)
        if sample is None:
            # Also try a point close to an end, since rounding can collapse
            # the midpoint for huge or extremely narrow numeric intervals.
            sample = (_representable(low + (high - low) / 4, low, high)
                      or _representable(high - (high - low) / 4, low, high))
        if sample is None:
            unprobed += 1
        else:
            add(sample)
    return list(result.values()), unprobed


def _facts(segments: list[str], number: int | float) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    at = facts
    for key in segments[:-1]:
        at = at.setdefault(key, {})
    at[segments[-1]] = number
    validate_facts(facts)
    return facts


def probe_table(table, rules: dict, *, as_of_date: str | None) -> dict[str, Any]:
    """Test sampled numeric points against the exact existing evaluator.

    Gap and overlap witnesses are meaningful only for the evaluated sample
    points. An absence of witnesses is *not* a whole-domain proof.
    """
    out = {
        "table_id": table.id,
        "hit_policy": table.hit_policy,
        "format": FORMAT,
        "status": "not_evaluated",
        "reason": None,
        "sampled_numeric_points": 0,
        "status_counts": {},
        "gap_witnesses": [],
        "overlap_witnesses": [],
        "first_precedence_competition_witnesses": [],
        "unsampled_numeric_regions": 0,
        "first_precedence_competition_count": 0,
        "source_case_facts_used": False,
        "actual_business_cases_evaluated": False,
        "source_basis_independently_verified": False,
        "whole_numeric_domain_coverage_verified": False,
        "business_policy_compliance_verified": False,
        "execution_authorized": False,
    }
    if as_of_date is None:
        out.update(reason="explicit_effective_date_required")
        return out
    validate_date(as_of_date)
    if as_of_date < table.effective_from:
        out.update(reason="table_not_yet_effective")
        return out
    if table.effective_until is not None and as_of_date >= table.effective_until:
        out.update(reason="table_expired")
        return out

    spec, reason = _supported(table, rules)
    if reason:
        out.update(reason=reason)
        return out
    path, segments, thresholds = spec
    points, unsampled = _candidates(thresholds)
    counts: Counter[str] = Counter()
    for value in points:
        check = evaluate_table(table, rules, _facts(segments, value), as_of_date)
        status = check["status"]
        counts[status] += 1
        witness = {"synthetic_value": value, "status": status}
        if status == "gap" and len(out["gap_witnesses"]) < MAX_WITNESSES:
            out["gap_witnesses"].append(witness)
        if status == "overlap" and len(out["overlap_witnesses"]) < MAX_WITNESSES:
            out["overlap_witnesses"].append(witness)
        # FIRST may select the first row despite later competing matches.
        if table.hit_policy == "FIRST" and sum(row["status"] == "matched" for row in check["rows"]) > 1:
            out["first_precedence_competition_count"] += 1
            if len(out["first_precedence_competition_witnesses"]) < MAX_WITNESSES:
                out["first_precedence_competition_witnesses"].append(witness)
    out.update(
        status="sampled", reason=None, fact_path=path,
        sampled_numeric_points=len(points),
        status_counts=dict(sorted(counts.items())),
        unsampled_numeric_regions=unsampled,
    )
    return out


def probe_tables(model, *, as_of_date: str | None = None) -> list[dict[str, Any]]:
    rules = {rule.id: rule for rule in model.business_rules}
    return [probe_table(table, rules, as_of_date=as_of_date)
            for table in model.decision_tables]


__all__ = ["FORMAT", "MAX_PROBES", "probe_table", "probe_tables"]
