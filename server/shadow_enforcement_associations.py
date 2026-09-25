from __future__ import annotations

"""Aggregate, non-causal shadow-enforcement/run-outcome associations.

A shadow preview is an adapter-reported simulation attached to a tool_call that
actually proceeded. This module relates those preview dispositions to the run's
structural terminal outcome. It never labels a preview a false positive/negative,
never estimates enforcement efficacy, and never returns run IDs or policy hashes.
"""

from collections import Counter, defaultdict
import re
from typing import Any, Iterable

from .context_execution_linkage import _agent_groups, _meta, _one_execution


_PROFILE_ID = "declared-policy-shadow-v1"
_DISPOSITIONS = (
    "candidate_deny",
    "candidate_pause_for_human_approval",
    "candidate_pause_for_prerequisite",
    "candidate_warn",
    "no_declared_enforcement_decision",
    "no_blocking_condition_observed",
    "indeterminate_policy_unavailable",
)
_DISPOSITION_SET = frozenset(_DISPOSITIONS)
_INTERRUPT_DISPOSITIONS = frozenset({
    "candidate_deny",
    "candidate_pause_for_human_approval",
    "candidate_pause_for_prerequisite",
})
_EXPLICIT_FAILURES = frozenset({"error", "denied", "cancelled"})
_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")


def _fraction(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _valid_shadow(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("profile_id") or "") != _PROFILE_ID:
        return False
    disposition = str(value.get("candidate_disposition") or "")
    if disposition not in _DISPOSITION_SET:
        return False
    if value.get("simulated_only") is not True:
        return False
    if value.get("actual_enforcement_enabled") is not False:
        return False
    if value.get("actual_blocking") is not False:
        return False
    return True


def _preview_records(raw_events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    invalid = 0
    for events in _agent_groups(raw_events):
        if not events:
            continue
        execution = _one_execution(events)
        run_key = str(execution.get("execution_id") or "")
        for event in events:
            meta, _trace = _meta(event)
            shadow = meta.get("shadow_enforcement")
            if shadow is None:
                continue
            if not _valid_shadow(shadow):
                invalid += 1
                continue
            disposition = str(shadow.get("candidate_disposition") or "")
            preview_family = str(shadow.get("family_key") or "")
            observed_family = str(execution.get("observed_family_key") or "")
            family_consistent: bool | None = None
            if preview_family and observed_family:
                family_consistent = preview_family == observed_family
            records.append({
                "_run_key": run_key,
                "disposition": disposition,
                "preview_available": bool(shadow.get("available")),
                "preview_family_key": preview_family or None,
                "observed_family_key": observed_family or None,
                "family_consistent": family_consistent,
                "observation_level": str(execution.get("observation_level") or "unknown"),
                "outcome_status": str(execution.get("outcome_status") or "unknown"),
                "approval_requested": execution.get("approval_requested") is True,
                "approval_received": execution.get("approval_received") is True,
                "policy_manifest_reported": bool(shadow.get("policy_manifest_sha256")),
            })
    return records, invalid


def _dedupe_runs(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_run: dict[str, dict[str, Any]] = {}
    for record in records:
        key = str(record.get("_run_key") or "")
        if key and key not in by_run:
            by_run[key] = record
    return list(by_run.values())


def _run_summary(records: list[dict[str, Any]], *, preview_event_count: int) -> dict[str, Any]:
    runs = _dedupe_runs(records)
    success = sum(1 for item in runs if item.get("outcome_status") == "success")
    failures = sum(1 for item in runs if item.get("outcome_status") in _EXPLICIT_FAILURES)
    unknown = sum(1 for item in runs if item.get("outcome_status") == "unknown")
    known = success + failures
    approvals_requested = sum(1 for item in runs if item.get("approval_requested"))
    approvals_received = sum(1 for item in runs if item.get("approval_received"))
    return {
        "preview_event_count": preview_event_count,
        "run_count": len(runs),
        "explicit_success_run_count": success,
        "explicit_failure_run_count": failures,
        "unknown_outcome_run_count": unknown,
        "known_terminal_outcome_run_count": known,
        "explicit_success_fraction_all_runs": _fraction(success, len(runs)),
        "explicit_failure_fraction_all_runs": _fraction(failures, len(runs)),
        "unknown_outcome_fraction_all_runs": _fraction(unknown, len(runs)),
        "success_rate_among_known_terminal_outcomes": _fraction(success, known),
        "failure_rate_among_known_terminal_outcomes": _fraction(failures, known),
        "approval_requested_run_count": approvals_requested,
        "approval_received_run_count": approvals_received,
        "approval_requested_fraction_all_runs": _fraction(approvals_requested, len(runs)),
        "policy_manifest_reported_preview_count": sum(1 for item in records if item.get("policy_manifest_reported")),
        "causal_interpretation": False,
        "false_positive_interpretation": False,
        "enforcement_effect_estimate": False,
    }


def shadow_associations_from_records(
    records: Iterable[dict[str, Any]],
    *,
    family_key: str = "",
    min_stratum_support: int = 2,
    max_strata: int = 50,
    invalid_preview_count: int = 0,
) -> dict[str, Any]:
    family = str(family_key or "").strip().lower()
    if family and not _FAMILY_KEY_RE.fullmatch(family):
        raise ValueError("invalid family_key")
    support = max(1, min(int(min_stratum_support), 100))
    stratum_limit = max(1, min(int(max_strata), 100))

    all_records = [dict(item) for item in records]
    if family:
        all_records = [
            item for item in all_records
            if item.get("observed_family_key") == family or item.get("preview_family_key") == family
        ]

    disposition_event_counts = Counter(str(item.get("disposition") or "unknown") for item in all_records)
    disposition_run_outcomes: dict[str, dict[str, Any]] = {}
    for disposition in _DISPOSITIONS:
        members = [item for item in all_records if item.get("disposition") == disposition]
        disposition_run_outcomes[disposition] = _run_summary(members, preview_event_count=len(members))

    interrupt_records = [item for item in all_records if item.get("disposition") in _INTERRUPT_DISPOSITIONS]
    noninterrupt_records = [item for item in all_records if item.get("disposition") not in _INTERRUPT_DISPOSITIONS]

    mismatch_count = sum(1 for item in all_records if item.get("family_consistent") is False)
    missing_family_count = sum(1 for item in all_records if not item.get("observed_family_key"))

    # Family/observation-level strata make the raw associations easier to inspect
    # without implying that workflow-mix differences are an enforcement effect.
    strata_members: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in all_records:
        if item.get("family_consistent") is False or not item.get("observed_family_key"):
            continue
        key = (
            str(item.get("observed_family_key") or ""),
            str(item.get("observation_level") or "unknown"),
            str(item.get("disposition") or ""),
        )
        strata_members[key].append(item)

    strata: list[dict[str, Any]] = []
    for (stratum_family, observation_level, disposition), members in strata_members.items():
        unique_run_count = len(_dedupe_runs(members))
        if unique_run_count < support:
            continue
        strata.append({
            "family_key": stratum_family,
            "observation_level": observation_level,
            "candidate_disposition": disposition,
            **_run_summary(members, preview_event_count=len(members)),
            "same_family": True,
            "same_observation_level": True,
        })
    strata.sort(
        key=lambda item: (
            int(item.get("run_count") or 0),
            int(item.get("preview_event_count") or 0),
            str(item.get("family_key") or ""),
        ),
        reverse=True,
    )
    strata = strata[:stratum_limit]

    return {
        "profile_id": _PROFILE_ID,
        "preview_event_count_considered": len(all_records),
        "run_count_with_shadow_preview": len(_dedupe_runs(all_records)),
        "disposition_preview_event_counts": dict(sorted(disposition_event_counts.items())),
        "disposition_run_outcomes": disposition_run_outcomes,
        "candidate_interrupt_run_outcomes": _run_summary(interrupt_records, preview_event_count=len(interrupt_records)),
        "candidate_noninterrupt_run_outcomes": _run_summary(noninterrupt_records, preview_event_count=len(noninterrupt_records)),
        "family_mismatch_preview_event_count": mismatch_count,
        "missing_observed_family_preview_event_count": missing_family_count,
        "invalid_or_legacy_preview_event_count": int(invalid_preview_count),
        "strata": strata,
        "strata_returned": len(strata),
        "minimum_stratum_run_support": support,
        "actual_enforcement_enabled": False,
        "actual_blocking_observed": False,
        "shadow_preview_is_adapter_reported": True,
        "preview_verified_by_server": False,
        "derived": True,
        "authoritative": False,
        "source": "canonical_agent_evidence",
        "causal_interpretation": False,
        "effect_estimate": False,
        "false_positive_rate_estimate": False,
        "interpretation": "descriptive association between adapter-reported shadow dispositions and the structural terminal outcome of the containing run; not an action-level outcome or evidence that blocking would help or harm",
        "limitations": [
            "shadow previews are adapter-reported simulations rather than server-attested policy decisions",
            "run-level outcomes are not action-level outcomes and may reflect many later events",
            "unknown outcomes remain unknown and are not counted as success or failure",
            "a run may appear in more than one disposition summary if it contained different shadow dispositions",
            "no causal, false-positive, false-negative, safety-benefit, or enforcement-effect conclusion is produced",
        ],
    }


def shadow_enforcement_outcome_associations(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    min_stratum_support: int = 2,
    max_strata: int = 50,
) -> dict[str, Any]:
    records, invalid = _preview_records(raw_events)
    return shadow_associations_from_records(
        records,
        family_key=family_key,
        min_stratum_support=min_stratum_support,
        max_strata=max_strata,
        invalid_preview_count=invalid,
    )
