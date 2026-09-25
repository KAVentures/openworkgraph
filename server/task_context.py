from __future__ import annotations

"""One read-only, provenance-preserving task-context contract for agents.

The service deliberately does not accept or interpret arbitrary natural-language
instructions. It resolves an existing procedural family from explicit identifiers
or generated structural steps, then composes the already-established governed
context pack without collapsing declared policy into observed behavior.
"""

import re
from typing import Any, Iterable

from .declared_policy import (
    DeclaredPolicyError,
    _validate_family,
    build_governed_context_pack,
    load_declared_policy_manifest,
)
from .procedural_context_pack import _validated_steps
from .procedural_memory import procedural_overview


_SCHEMA_VERSION = "1.0"
_MAX_CANDIDATES = 8
_TASK_FAMILY_RE = re.compile(r"^(?:email|github)\.[a-z0-9][a-z0-9._-]{0,126}$")


class TaskContextError(ValueError):
    pass


def _canonical_task_family(value: str) -> str:
    task_family = str(value or "").strip().lower()
    if not task_family:
        return ""
    if not _TASK_FAMILY_RE.fullmatch(task_family):
        raise TaskContextError("task_family must be a canonical email.* or github.* family")
    return f"human:{task_family}"


def _active_policy_families(manifest: dict[str, Any]) -> set[str]:
    return {
        str(item.get("family_key") or "")
        for item in (manifest.get("policies") or [])
        if isinstance(item, dict) and item.get("status") == "active" and item.get("family_key")
    }


def _family_inventory(raw_events: list[dict[str, Any]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
    overview = procedural_overview(raw_events, min_support=1)
    active_policy_families = _active_policy_families(manifest)
    inventory: dict[str, dict[str, Any]] = {}
    for item in overview.get("families") or []:
        family_key = str(item.get("family_key") or "")
        if not family_key:
            continue
        inventory[family_key] = {
            "family_key": family_key,
            "actor_kind": item.get("actor_kind"),
            "family_basis": item.get("family_basis"),
            "execution_count": int(item.get("execution_count") or 0),
            "positive_example_count": int(item.get("positive_example_count") or 0),
            "confidence": item.get("confidence"),
            "dominant_sequence": list(item.get("dominant_sequence") or []),
            "declared_policy_active": family_key in active_policy_families,
            "observed": True,
        }
    for family_key in active_policy_families:
        if family_key in inventory:
            continue
        inventory[family_key] = {
            "family_key": family_key,
            "actor_kind": "human" if family_key.startswith("human:") else "agent",
            "family_basis": "declared_policy_only",
            "execution_count": 0,
            "positive_example_count": 0,
            "confidence": None,
            "dominant_sequence": [],
            "declared_policy_active": True,
            "observed": False,
        }
    return sorted(
        inventory.values(),
        key=lambda item: (
            not bool(item.get("declared_policy_active")),
            -int(item.get("execution_count") or 0),
            str(item.get("family_key") or ""),
        ),
    )


def _slim_candidate(item: dict[str, Any], *, match: str = "available") -> dict[str, Any]:
    return {
        "family_key": item.get("family_key"),
        "actor_kind": item.get("actor_kind"),
        "family_basis": item.get("family_basis"),
        "execution_count": int(item.get("execution_count") or 0),
        "positive_example_count": int(item.get("positive_example_count") or 0),
        "confidence": item.get("confidence"),
        "declared_policy_active": bool(item.get("declared_policy_active")),
        "observed": bool(item.get("observed")),
        "match": match,
    }


def resolve_task_family(
    raw_events: list[dict[str, Any]],
    *,
    manifest: dict[str, Any],
    family_key: str = "",
    task_family: str = "",
    current_steps: Iterable[str] = (),
) -> dict[str, Any]:
    """Resolve a family conservatively without interpreting arbitrary free text."""
    inventory = _family_inventory(raw_events, manifest)
    by_key = {str(item["family_key"]): item for item in inventory}

    explicit = str(family_key or "").strip().lower()
    if explicit:
        try:
            resolved = _validate_family(explicit)
        except DeclaredPolicyError as exc:
            raise TaskContextError("invalid family_key") from exc
        item = by_key.get(resolved)
        return {
            "status": "resolved",
            "mode": "explicit_family_key",
            "family_key": resolved,
            "family_known": item is not None,
            "candidates": [_slim_candidate(item, match="exact") ] if item else [],
            "free_text_resolution_used": False,
            "input_echoed": False,
        }

    canonical = _canonical_task_family(task_family)
    if canonical:
        item = by_key.get(canonical)
        return {
            "status": "resolved",
            "mode": "canonical_task_family",
            "family_key": canonical,
            "family_known": item is not None,
            "candidates": [_slim_candidate(item, match="exact") ] if item else [],
            "free_text_resolution_used": False,
            "input_echoed": False,
        }

    try:
        steps, _unused_after = _validated_steps(current_steps, "")
    except ValueError as exc:
        raise TaskContextError("invalid current structural steps") from exc

    if len(steps) < 2:
        return {
            "status": "insufficient_input",
            "mode": "structural_prefix",
            "family_key": None,
            "family_known": False,
            "candidates": [_slim_candidate(item) for item in inventory[:_MAX_CANDIDATES]],
            "free_text_resolution_used": False,
            "input_echoed": False,
            "minimum_structural_steps_for_auto_resolution": 2,
        }

    matches: list[dict[str, Any]] = []
    for item in inventory:
        sequence = list(item.get("dominant_sequence") or [])
        if len(sequence) >= len(steps) and sequence[: len(steps)] == steps:
            matches.append(item)

    if len(matches) == 1:
        item = matches[0]
        return {
            "status": "resolved",
            "mode": "unique_structural_prefix",
            "family_key": item["family_key"],
            "family_known": True,
            "candidates": [_slim_candidate(item, match="unique_structural_prefix")],
            "free_text_resolution_used": False,
            "input_echoed": False,
            "matched_structural_step_count": len(steps),
        }
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "mode": "structural_prefix",
            "family_key": None,
            "family_known": False,
            "candidates": [
                _slim_candidate(item, match="structural_prefix")
                for item in matches[:_MAX_CANDIDATES]
            ],
            "free_text_resolution_used": False,
            "input_echoed": False,
            "matched_structural_step_count": len(steps),
        }
    return {
        "status": "not_found",
        "mode": "structural_prefix",
        "family_key": None,
        "family_known": False,
        "candidates": [_slim_candidate(item) for item in inventory[:_MAX_CANDIDATES]],
        "free_text_resolution_used": False,
        "input_echoed": False,
        "matched_structural_step_count": len(steps),
    }


def _authority_model() -> dict[str, Any]:
    return {
        "declared_policy": "normative_input_when_active",
        "observed_procedure": "non_authoritative_derived_evidence",
        "policy_observation_comparison": "derived_assessment_not_enforcement",
        "family_resolution": "derived_routing_only",
        "policy_inferred_from_behavior": False,
        "observed_behavior_becomes_policy": False,
        "automatic_execution": False,
        "automatic_policy_enforcement": False,
    }


def build_task_context(
    raw_events: list[dict[str, Any]],
    *,
    family_key: str = "",
    task_family: str = "",
    current_steps: Iterable[str] = (),
    after_step: str = "",
    min_support: int = 2,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
    manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one stable task-context response with explicit authority boundaries."""
    loaded = manifest if manifest is not None else load_declared_policy_manifest()
    try:
        normalized_steps, normalized_after = _validated_steps(current_steps, after_step)
    except ValueError as exc:
        raise TaskContextError("invalid structural steps") from exc

    resolution = resolve_task_family(
        raw_events,
        manifest=loaded,
        family_key=family_key,
        task_family=task_family,
        current_steps=normalized_steps,
    )
    base = {
        "schema_version": _SCHEMA_VERSION,
        "resolution": resolution,
        "authority_model": _authority_model(),
        "read_only": True,
        "writes_performed": False,
        "free_text_task_matching": False,
        "automatic_context_injection": False,
    }
    resolved_family = resolution.get("family_key") if resolution.get("status") == "resolved" else None
    if not resolved_family:
        return {**base, "context_available": False, "task_context": None}

    try:
        governed = build_governed_context_pack(
            raw_events,
            family_key=str(resolved_family),
            current_steps=normalized_steps,
            after_step=normalized_after,
            min_support=min_support,
            run_limit=run_limit,
            section_limit=section_limit,
            max_steps_per_run=max_steps_per_run,
            max_evidence_refs_per_item=max_evidence_refs_per_item,
            manifest=loaded,
        )
    except (DeclaredPolicyError, TypeError, ValueError) as exc:
        raise TaskContextError("unable to build governed task context") from exc

    policy = governed.get("declared_policy") if isinstance(governed.get("declared_policy"), dict) else None
    observed = governed.get("observed_context") if isinstance(governed.get("observed_context"), dict) else {}
    comparison = governed.get("policy_observation_comparison")
    task_context = {
        "family_key": resolved_family,
        "policy": {
            "status": "active" if policy else "not_declared",
            "authority_class": "declared_normative" if policy else "none",
            "authoritative_as_declared_input": bool(policy),
            "policy_inferred_from_behavior": False,
            "cryptographic_verification_asserted_by_task_context": False,
            "manifest_sha256": governed.get("manifest_sha256"),
            "manifest_present": bool(governed.get("manifest_present")),
            "item": policy,
        },
        "observed_procedure": {
            **observed,
            "authority_class": "observed_evidence",
            "authoritative": False,
            "prescriptive": False,
        },
        "policy_observation_comparison": comparison,
        "provenance": {
            "policy_source": "declared_policy_manifest",
            "observed_source": "canonical_evidence_via_procedural_memory",
            "comparison_source": "derived_structural_comparison" if comparison is not None else None,
            "evidence_is_canonical": True,
            "memory_is_regeneratable": True,
            "persisted_learned_procedure": False,
        },
    }
    return {**base, "context_available": True, "task_context": task_context}
