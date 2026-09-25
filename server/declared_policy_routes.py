from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .declared_policy import (
    DeclaredPolicyError,
    active_policy_for_family,
    build_governed_context_pack,
    compare_policy_to_observations,
    load_declared_policy_manifest,
)
from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence


router = APIRouter()


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


def _load_manifest() -> dict[str, Any]:
    try:
        return load_declared_policy_manifest()
    except DeclaredPolicyError as exc:
        raise HTTPException(status_code=422, detail="invalid declared policy manifest") from exc


def _raw(limit: int, since: str | None) -> list[dict[str, Any]]:
    try:
        return load_recent_evidence(limit=max(1, min(int(limit), 25_000)), since=since)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid declared-policy query") from exc


def _steps(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [item.strip().lower() for item in value.split(",") if item.strip()]
    if len(parts) > 48:
        raise HTTPException(status_code=422, detail="too many structural steps")
    return parts


@router.get("/v1/declared-policies")
def get_declared_policies(request: Request) -> dict[str, Any]:
    _require_api_read_bearer(request)
    return _load_manifest()


@router.get("/v1/declared-policies/compare")
def compare_declared_policy(
    request: Request,
    family_key: str,
    since: str | None = None,
    limit: int = 10_000,
    divergence_limit: int = 10,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    manifest = _load_manifest()
    try:
        policy = active_policy_for_family(manifest, family_key)
    except DeclaredPolicyError as exc:
        raise HTTPException(status_code=422, detail="invalid declared-policy query") from exc
    if policy is None:
        return {
            "family_key": family_key,
            "declared_policy_status": "not_declared",
            "policy": None,
            "comparison": None,
            "manifest_present": bool(manifest.get("manifest_present")),
        }
    raw = _raw(limit, since)
    try:
        comparison = compare_policy_to_observations(
            raw,
            policy=policy,
            divergence_limit=max(1, min(int(divergence_limit), 20)),
        )
    except (DeclaredPolicyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid declared-policy query") from exc
    return {
        "family_key": family_key,
        "declared_policy_status": "active",
        "policy": policy,
        "comparison": comparison,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
    }


@router.get("/v1/procedural-memory/governed-context-pack")
def get_governed_context_pack(
    request: Request,
    family_key: str,
    current_steps: str = "",
    after_step: str = "",
    since: str | None = None,
    limit: int = 10_000,
    min_support: int = 2,
    run_limit: int = 3,
    section_limit: int = 3,
    max_steps_per_run: int = 16,
    max_evidence_refs_per_item: int = 2,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    manifest = _load_manifest()
    try:
        payload = build_governed_context_pack(
            raw,
            family_key=family_key,
            current_steps=_steps(current_steps),
            after_step=after_step,
            min_support=min_support,
            run_limit=run_limit,
            section_limit=section_limit,
            max_steps_per_run=max_steps_per_run,
            max_evidence_refs_per_item=max_evidence_refs_per_item,
            manifest=manifest,
        )
    except (DeclaredPolicyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid governed-context query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "policy_manifest_write_api_available": False,
    }
