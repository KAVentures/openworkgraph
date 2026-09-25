from __future__ import annotations

"""Descriptive outcome associations for task-context-linked agent executions.

This module intentionally performs *observational* aggregation only. It does not
estimate causal effects, rank workflows, or claim that OpenWorkGraph context
changed an outcome. The strongest comparisons are restricted to the same
observed workflow family and observation level, and small cohorts suppress rate
differences while still exposing their raw support counts.
"""

from collections import Counter, defaultdict
from datetime import datetime
import re
from typing import Any, Iterable

from .context_execution_linkage import derive_context_executions


_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_EXPLICIT_FAILURES = frozenset({"error", "denied", "cancelled"})
_LINKAGE_GROUPS = (
    "context_resolved",
    "context_available_unresolved",
    "preflight_unavailable",
    "not_observed",
)
_COMPARATORS = (
    "preflight_unavailable",
    "context_available_unresolved",
    "not_observed",
)


def _fraction(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _percentage_points(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round((left - right) * 100.0, 2)


def _iso_timestamp(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _time_bounds(items: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    values = sorted(str(item.get("started_at") or "") for item in items if item.get("started_at"))
    return (values[0], values[-1]) if values else (None, None)


def _time_windows_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool | None:
    l_start = _iso_timestamp(left.get("first_started_at"))
    l_end = _iso_timestamp(left.get("last_started_at"))
    r_start = _iso_timestamp(right.get("first_started_at"))
    r_end = _iso_timestamp(right.get("last_started_at"))
    if None in {l_start, l_end, r_start, r_end}:
        return None
    return bool(max(float(l_start), float(r_start)) <= min(float(l_end), float(r_end)))


def _group_summary(items: list[dict[str, Any]], *, group: str) -> dict[str, Any]:
    success = sum(1 for item in items if item.get("outcome_status") == "success")
    failures = sum(1 for item in items if item.get("outcome_status") in _EXPLICIT_FAILURES)
    unknown = sum(1 for item in items if item.get("outcome_status") == "unknown")
    known = success + failures
    approval_requested = sum(1 for item in items if item.get("approval_requested") is True)
    approval_received = sum(1 for item in items if item.get("approval_received") is True)
    policy_reported = sum(1 for item in items if bool(item.get("policy_manifest_sha256")))
    first_started, last_started = _time_bounds(items)
    return {
        "group": group,
        "run_count": len(items),
        "explicit_success_count": success,
        "explicit_failure_count": failures,
        "unknown_outcome_count": unknown,
        "known_terminal_outcome_count": known,
        "explicit_success_fraction_all_runs": _fraction(success, len(items)),
        "explicit_failure_fraction_all_runs": _fraction(failures, len(items)),
        "unknown_outcome_fraction_all_runs": _fraction(unknown, len(items)),
        "success_rate_among_known_terminal_outcomes": _fraction(success, known),
        "failure_rate_among_known_terminal_outcomes": _fraction(failures, known),
        "approval_requested_run_count": approval_requested,
        "approval_received_run_count": approval_received,
        "approval_requested_fraction_all_runs": _fraction(approval_requested, len(items)),
        "policy_manifest_reported_count": policy_reported,
        "policy_manifest_reported_fraction_all_runs": _fraction(policy_reported, len(items)),
        "first_started_at": first_started,
        "last_started_at": last_started,
    }


def _comparator_semantics(group: str) -> dict[str, Any]:
    if group == "preflight_unavailable":
        return {
            "context_exposure_known": True,
            "description": "preflight was attempted but context service was reported unavailable",
        }
    if group == "context_available_unresolved":
        return {
            "context_exposure_known": True,
            "description": "preflight service was available but no task context was resolved",
        }
    return {
        "context_exposure_known": False,
        "description": "no preflight linkage was observed; historical context exposure is unknown",
    }


def _comparison(
    resolved: dict[str, Any],
    comparator: dict[str, Any],
    *,
    comparator_group: str,
    min_group_support: int,
    min_known_outcomes: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    if int(resolved["run_count"]) < min_group_support:
        reasons.append("resolved_context_group_below_minimum_support")
    if int(comparator["run_count"]) < min_group_support:
        reasons.append("comparator_group_below_minimum_support")
    if int(resolved["known_terminal_outcome_count"]) < min_known_outcomes:
        reasons.append("resolved_context_known_outcomes_below_minimum")
    if int(comparator["known_terminal_outcome_count"]) < min_known_outcomes:
        reasons.append("comparator_known_outcomes_below_minimum")

    supported = not reasons
    semantics = _comparator_semantics(comparator_group)
    output: dict[str, Any] = {
        "resolved_group": "context_resolved",
        "comparator_group": comparator_group,
        "comparison_status": "descriptive_only" if supported else "insufficient_support",
        "minimum_group_support": min_group_support,
        "minimum_known_terminal_outcomes": min_known_outcomes,
        "support_reasons": reasons,
        "context_exposure_known_for_comparator": semantics["context_exposure_known"],
        "comparator_interpretation": semantics["description"],
        "time_windows_overlap": _time_windows_overlap(resolved, comparator),
        "causal_interpretation": False,
        "effect_estimate": False,
    }
    if supported:
        output.update({
            "observed_explicit_success_fraction_difference_pp": _percentage_points(
                resolved.get("explicit_success_fraction_all_runs"),
                comparator.get("explicit_success_fraction_all_runs"),
            ),
            "observed_explicit_failure_fraction_difference_pp": _percentage_points(
                resolved.get("explicit_failure_fraction_all_runs"),
                comparator.get("explicit_failure_fraction_all_runs"),
            ),
            "observed_unknown_outcome_fraction_difference_pp": _percentage_points(
                resolved.get("unknown_outcome_fraction_all_runs"),
                comparator.get("unknown_outcome_fraction_all_runs"),
            ),
            "observed_failure_rate_known_outcomes_difference_pp": _percentage_points(
                resolved.get("failure_rate_among_known_terminal_outcomes"),
                comparator.get("failure_rate_among_known_terminal_outcomes"),
            ),
            "observed_approval_request_fraction_difference_pp": _percentage_points(
                resolved.get("approval_requested_fraction_all_runs"),
                comparator.get("approval_requested_fraction_all_runs"),
            ),
        })
    else:
        output.update({
            "observed_explicit_success_fraction_difference_pp": None,
            "observed_explicit_failure_fraction_difference_pp": None,
            "observed_unknown_outcome_fraction_difference_pp": None,
            "observed_failure_rate_known_outcomes_difference_pp": None,
            "observed_approval_request_fraction_difference_pp": None,
        })
    return output


def _grouped_summaries(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {name: [] for name in _LINKAGE_GROUPS}
    for item in items:
        state = str(item.get("linkage_status") or "")
        if state in grouped:
            grouped[state].append(item)
    return {name: _group_summary(grouped[name], group=name) for name in _LINKAGE_GROUPS}


def _resolved_policy_breakdown(items: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [item for item in items if item.get("linkage_status") == "context_resolved"]
    with_policy = [item for item in resolved if bool(item.get("policy_manifest_sha256"))]
    without_policy_hash = [item for item in resolved if not item.get("policy_manifest_sha256")]
    return {
        "with_policy_manifest_reported": _group_summary(with_policy, group="resolved_context_with_policy_manifest"),
        "without_policy_manifest_hash": _group_summary(without_policy_hash, group="resolved_context_without_policy_manifest_hash"),
        "interpretation": "within resolved context only; absence of a policy hash on unlinked/unavailable runs is not interpreted as policy absence",
    }


def associations_from_executions(
    executions: Iterable[dict[str, Any]],
    *,
    family_key: str = "",
    min_group_support: int = 5,
    min_known_outcomes: int = 3,
    min_stratum_support: int = 2,
    max_strata: int = 50,
) -> dict[str, Any]:
    """Aggregate context/outcome associations from privacy-safe execution records."""
    family = str(family_key or "").strip().lower()
    if family and not _FAMILY_KEY_RE.fullmatch(family):
        raise ValueError("invalid family_key")
    group_support = max(2, min(int(min_group_support), 100))
    known_support = max(1, min(int(min_known_outcomes), 100))
    stratum_support = max(1, min(int(min_stratum_support), 100))
    stratum_limit = max(1, min(int(max_strata), 100))

    all_items = [dict(item) for item in executions]
    if family:
        all_items = [
            item for item in all_items
            if item.get("observed_family_key") == family or item.get("preflight_family_key") == family
        ]

    linkage_counts = Counter(str(item.get("linkage_status") or "unknown") for item in all_items)
    excluded = {
        "conflicting_linkage": sum(1 for item in all_items if item.get("linkage_status") == "conflicting_assertions"),
        "family_mismatch": sum(1 for item in all_items if item.get("family_consistent") is False),
        "missing_observed_family": sum(1 for item in all_items if not item.get("observed_family_key")),
    }

    # Overall summaries are descriptive only. We deliberately do not calculate
    # pooled context-vs-outcome differences across workflow families because
    # family mix can strongly confound those numbers.
    overall_eligible = [item for item in all_items if item.get("linkage_status") != "conflicting_assertions"]
    overall_groups = _grouped_summaries(overall_eligible)

    # Comparative strata require a structural observed family. A resolved
    # preflight that disagrees with the observed family is excluded rather than
    # silently reassigned to whichever family produces a more favorable result.
    comparative_pool = [
        item for item in all_items
        if item.get("linkage_status") != "conflicting_assertions"
        and item.get("family_consistent") is not False
        and item.get("observed_family_key")
    ]
    strata_members: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in comparative_pool:
        key = (
            str(item.get("observed_family_key") or ""),
            str(item.get("observation_level") or "unknown"),
        )
        strata_members[key].append(item)

    strata: list[dict[str, Any]] = []
    for (stratum_family, observation_level), members in strata_members.items():
        if len(members) < stratum_support:
            continue
        groups = _grouped_summaries(members)
        resolved = groups["context_resolved"]
        comparisons = [
            _comparison(
                resolved,
                groups[comparator],
                comparator_group=comparator,
                min_group_support=group_support,
                min_known_outcomes=known_support,
            )
            for comparator in _COMPARATORS
        ]
        strata.append({
            "family_key": stratum_family,
            "observation_level": observation_level,
            "run_count": len(members),
            "groups": groups,
            "resolved_context_policy_breakdown": _resolved_policy_breakdown(members),
            "comparisons": comparisons,
            "same_family": True,
            "same_observation_level": True,
            "causal_interpretation": False,
        })

    strata.sort(
        key=lambda item: (
            int(item.get("run_count") or 0),
            str(item.get("family_key") or ""),
            str(item.get("observation_level") or ""),
        ),
        reverse=True,
    )
    strata = strata[:stratum_limit]

    return {
        "execution_count_considered": len(all_items),
        "linkage_status_counts": dict(sorted(linkage_counts.items())),
        "overall_groups": overall_groups,
        "pooled_group_difference_produced": False,
        "pooled_group_difference_reason": "cross-family pooled outcome differences are suppressed to reduce workflow-mix confounding",
        "resolved_context_policy_breakdown_overall": _resolved_policy_breakdown(overall_eligible),
        "strata": strata,
        "strata_returned": len(strata),
        "excluded_from_stratified_comparisons": excluded,
        "minimum_group_support_for_differences": group_support,
        "minimum_known_terminal_outcomes_for_differences": known_support,
        "minimum_stratum_support": stratum_support,
        "derived": True,
        "authoritative": False,
        "source": "canonical_agent_evidence",
        "linkage_is_adapter_reported": True,
        "context_snapshot_server_attested": False,
        "causal_interpretation": False,
        "effect_estimate": False,
        "interpretation": "descriptive associations between adapter-reported task-context linkage and observed structural outcomes; not evidence that context caused an outcome",
        "limitations": [
            "task-context linkage is adapter-reported rather than server-attested consumption",
            "assignment to context availability/resolution is not randomized",
            "temporal, model, operator, workload, and deployment changes may confound observed differences",
            "unknown outcomes remain unknown and are not counted as success or failure",
            "no-linkage-observed does not mean no context was supplied",
        ],
    }


def context_outcome_associations(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    min_group_support: int = 5,
    min_known_outcomes: int = 3,
    min_stratum_support: int = 2,
    max_strata: int = 50,
) -> dict[str, Any]:
    """Derive aggregate task-context/outcome associations from canonical evidence."""
    return associations_from_executions(
        derive_context_executions(raw_events),
        family_key=family_key,
        min_group_support=min_group_support,
        min_known_outcomes=min_known_outcomes,
        min_stratum_support=min_stratum_support,
        max_strata=max_strata,
    )
