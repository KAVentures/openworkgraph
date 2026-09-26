from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .local_auth import bearer_matches
from .procedural_context_pack import build_context_pack
from .procedural_feedback import readable_feedback
from .procedural_memory import (
    approval_patterns,
    failure_patterns,
    load_recent_evidence,
    next_steps,
    procedural_overview,
    similar_runs,
)


router = APIRouter()
_INTERNAL_ID_KEYS = frozenset({"session_id", "run_id", "trace_id", "span_id", "parent_span_id"})


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


def _raw(limit: int, since: str | None) -> list[dict[str, Any]]:
    try:
        return load_recent_evidence(limit=limit, since=since)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-memory query") from exc


def _step_list(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [item.strip().lower() for item in value.split(",") if item.strip()]
    if len(parts) > 48:
        raise HTTPException(status_code=422, detail="too many structural steps")
    return parts


def _strip_internal_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _strip_internal_ids(child)
            for key, child in value.items()
            if str(key) not in _INTERNAL_ID_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_ids(item) for item in value]
    return value


def _derived_response(payload: dict[str, Any], raw: list[dict[str, Any]]) -> dict[str, Any]:
    protected = _strip_internal_ids(payload)
    return {
        **protected,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "memory_is_regeneratable": True,
    }


@router.get("/v1/procedural-memory")
def get_procedural_memory(
    request: Request,
    since: str | None = None,
    limit: int = 25_000,
    min_support: int = 2,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    return _derived_response(procedural_overview(raw, min_support=min_support), raw)


@router.get("/v1/procedural-memory/similar-runs")
def get_similar_runs(
    request: Request,
    family_key: str,
    current_steps: str = "",
    since: str | None = None,
    limit: int = 25_000,
    result_limit: int = 10,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    try:
        payload = similar_runs(
            raw,
            family_key=family_key,
            current_steps=_step_list(current_steps),
            limit=result_limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-memory query") from exc
    return _derived_response(payload, raw)


@router.get("/v1/procedural-memory/failure-patterns")
def get_failure_patterns(
    request: Request,
    family_key: str = "",
    since: str | None = None,
    limit: int = 25_000,
    min_support: int = 2,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    try:
        payload = failure_patterns(raw, family_key=family_key, min_support=min_support)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-memory query") from exc
    return _derived_response(payload, raw)


@router.get("/v1/procedural-memory/next-steps")
def get_next_observed_steps(
    request: Request,
    family_key: str,
    prefix: str = "",
    after_step: str = "",
    since: str | None = None,
    limit: int = 25_000,
    min_support: int = 2,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    try:
        payload = next_steps(
            raw,
            family_key=family_key,
            prefix=_step_list(prefix),
            after_step=after_step,
            min_support=min_support,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-memory query") from exc
    return _derived_response(payload, raw)


@router.get("/v1/procedural-memory/approval-patterns")
def get_approval_patterns(
    request: Request,
    family_key: str = "",
    since: str | None = None,
    limit: int = 25_000,
    min_support: int = 2,
) -> dict[str, Any]:
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    try:
        payload = approval_patterns(raw, family_key=family_key, min_support=min_support)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-memory query") from exc
    return _derived_response(payload, raw)


@router.get("/v1/procedural-memory/context-pack")
def get_procedural_context_pack(
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
    """Return a small observational workflow-memory pack for one family.

    The pack is intentionally read-only, non-prescriptive, and contains no
    organizational policy. Repeated behavior remains evidence rather than rules.
    """
    _require_api_read_bearer(request)
    bounded_limit = max(1, min(int(limit), 25_000))
    raw = _raw(bounded_limit, since)
    try:
        payload = build_context_pack(
            raw,
            family_key=family_key,
            current_steps=_step_list(current_steps),
            after_step=after_step,
            min_support=min_support,
            run_limit=run_limit,
            section_limit=section_limit,
            max_steps_per_run=max_steps_per_run,
            max_evidence_refs_per_item=max_evidence_refs_per_item,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid procedural-context-pack query") from exc
    return _derived_response(payload, raw)


@router.get("/v1/procedural-memory/readable-feedback")
def get_readable_feedback(
    request: Request,
    family_key: str,
    current_steps: str = "",
    after_step: str = "",
    since: str | None = None,
    limit: int = 25_000,
    result_limit: int = 8,
    min_support: int = 2,
) -> dict[str, Any]:
    """Return privacy-safe semantic human steps over stable structural identities."""
    _require_api_read_bearer(request)
    raw = _raw(limit, since)
    try:
        payload = readable_feedback(
            raw,
            family_key=family_key,
            current_steps=current_steps,
            after_step=after_step,
            min_support=min_support,
            run_limit=result_limit,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid readable procedural-feedback query") from exc
    return _derived_response(payload, raw)
