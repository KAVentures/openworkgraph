from __future__ import annotations

"""Bounded read-only procedural context assembled from procedural-memory views.

This module intentionally does not create policy, instructions, learned state, or
agent actions. It composes already-derived observational memory into a small
payload suitable for an agent context window while preserving the evidence and
authority semantics established by procedural_memory.
"""

from typing import Any, Iterable

from .procedural_memory import (
    approval_patterns,
    failure_patterns,
    next_steps,
    procedural_overview,
    similar_runs,
)


_MAX_RUNS = 5
_MAX_SECTION_ITEMS = 5
_MAX_STEPS_PER_RUN = 24
_MAX_EVIDENCE_REFS = 4


def _bounded_int(value: int, *, minimum: int, maximum: int) -> int:
    return max(minimum, min(int(value), maximum))


def _slim_family(item: dict[str, Any], *, max_steps: int) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "family_key",
            "actor_kind",
            "family_basis",
            "execution_count",
            "positive_example_count",
            "observed_completion_count",
            "explicit_success_count",
            "explicit_failure_count",
            "unknown_outcome_count",
            "dominant_sequence_support",
            "dominant_sequence_fraction",
            "reusable_candidate",
            "confidence",
            "derived",
            "authoritative",
            "needs_review",
        )
    } | {
        "dominant_sequence": list(item.get("dominant_sequence") or [])[:max_steps],
    }


def _slim_run(
    item: dict[str, Any],
    *,
    max_steps: int,
    max_evidence_refs: int,
) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "execution_id",
            "actor_kind",
            "started_at",
            "ended_at",
            "duration_seconds",
            "outcome_status",
            "outcome_basis",
            "observation_level",
            "similarity_score",
            "matched_on",
            "derived",
            "authoritative",
            "needs_review",
        )
    } | {
        "steps": list(item.get("steps") or [])[:max_steps],
        "evidence_refs": list(item.get("evidence_refs") or [])[:max_evidence_refs],
    }


def _slim_failure(item: dict[str, Any], *, max_evidence_refs: int) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "pattern_id",
            "preceding_step",
            "failure_step",
            "explicit_status",
            "support",
            "family_execution_count",
            "observed_fraction",
            "interpretation",
            "derived",
            "authoritative",
            "needs_review",
        )
    } | {
        "evidence_refs": list(item.get("evidence_refs") or [])[:max_evidence_refs],
    }


def _slim_next_step(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in ("step", "support", "opportunities", "observed_fraction", "interpretation")
    }


def _slim_approval(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "pattern_id",
            "preceding_step",
            "executions_with_request",
            "family_execution_count",
            "observed_fraction",
            "decision_status_counts",
            "normative_requirement",
            "interpretation",
            "derived",
            "authoritative",
            "needs_review",
        )
    }


def build_context_pack(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str,
    current_steps: Iterable[str] = (),
    after_step: str = "",
    min_support: int = 2,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
) -> dict[str, Any]:
    """Compose a deterministic, bounded context pack for one procedural family.

    All component functions remain observational. This composer deliberately adds
    explicit authority/policy metadata so a consumer cannot reasonably interpret
    repeated behavior as an organizational rule merely because it was bundled for
    convenient retrieval.
    """
    runs_cap = _bounded_int(run_limit, minimum=1, maximum=_MAX_RUNS)
    section_cap = _bounded_int(section_limit, minimum=1, maximum=_MAX_SECTION_ITEMS)
    steps_cap = _bounded_int(max_steps_per_run, minimum=1, maximum=_MAX_STEPS_PER_RUN)
    refs_cap = _bounded_int(max_evidence_refs_per_item, minimum=0, maximum=_MAX_EVIDENCE_REFS)
    support = _bounded_int(min_support, minimum=2, maximum=100)
    normalized_steps = [str(step).strip().lower() for step in current_steps if str(step).strip()]

    overview = procedural_overview(raw_events, min_support=1)
    family = next(
        (item for item in overview.get("families") or [] if item.get("family_key") == family_key),
        None,
    )

    similar = similar_runs(
        raw_events,
        family_key=family_key,
        current_steps=normalized_steps,
        limit=runs_cap,
    )
    failures = failure_patterns(raw_events, family_key=family_key, min_support=support)
    observed_next = next_steps(
        raw_events,
        family_key=family_key,
        prefix=normalized_steps,
        after_step=after_step,
        min_support=support,
    )
    approvals = approval_patterns(raw_events, family_key=family_key, min_support=support)

    raw_runs = list(similar.get("runs") or [])
    raw_failures = list(failures.get("patterns") or [])
    raw_next = list(observed_next.get("candidates") or [])
    raw_approvals = list(approvals.get("patterns") or [])

    pack = {
        "family_key": family_key,
        "family_found": family is not None,
        "family": _slim_family(family, max_steps=steps_cap) if family else None,
        "current_structural_steps": normalized_steps[:steps_cap],
        "similar_runs": [
            _slim_run(item, max_steps=steps_cap, max_evidence_refs=refs_cap)
            for item in raw_runs[:runs_cap]
        ],
        "failure_patterns": [
            _slim_failure(item, max_evidence_refs=refs_cap)
            for item in raw_failures[:section_cap]
        ],
        "next_observed_steps": [
            _slim_next_step(item)
            for item in raw_next[:section_cap]
        ],
        "approval_patterns": [
            _slim_approval(item)
            for item in raw_approvals[:section_cap]
        ],
        "authority": {
            "observational_only": True,
            "authoritative": False,
            "prescriptive": False,
            "policy_status": "not_provided",
            "policy_inferred": False,
            "requires_human_review_for_policy": True,
            "interpretation": "workflow evidence for context; not instructions, policy, or ground truth",
        },
        "outcome_semantics": {
            "human_completion_label": "observed_completion",
            "human_completion_is_success": False,
            "agent_success_requires_explicit_terminal_evidence": True,
            "unknown_outcomes_remain_unknown": True,
        },
        "budget": {
            "max_similar_runs": runs_cap,
            "max_items_per_pattern_section": section_cap,
            "max_steps_per_run": steps_cap,
            "max_evidence_refs_per_item": refs_cap,
            "minimum_pattern_support": support,
            "hard_capped": True,
        },
        "truncation": {
            "similar_runs": len(raw_runs) > runs_cap,
            "failure_patterns": len(raw_failures) > section_cap,
            "next_observed_steps": len(raw_next) > section_cap,
            "approval_patterns": len(raw_approvals) > section_cap,
        },
        "derived": True,
        "authoritative": False,
        "prescriptive": False,
        "source": "canonical_evidence_via_procedural_memory",
        "persisted_learned_state": False,
    }
    return pack
