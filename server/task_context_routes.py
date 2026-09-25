from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .declared_policy import DeclaredPolicyError, load_declared_policy_manifest
from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence
from .task_context import TaskContextError, build_task_context


router = APIRouter()
_INTERNAL_ID_KEYS = frozenset({"session_id", "run_id", "trace_id", "span_id", "parent_span_id"})


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


def _steps(value: str | None) -> list[str]:
    if not value:
        return []
    parts = [item.strip().lower() for item in value.split(",") if item.strip()]
    if len(parts) > 48:
        raise HTTPException(status_code=422, detail="too many structural steps")
    return parts


def _strip_internal_ids(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _strip_internal_ids(child)
            for key, child in value.items()
            if str(key) not in _INTERNAL_ID_KEYS
        }
    if isinstance(value, list):
        return [_strip_internal_ids(item) for item in value]
    return value


@router.get("/v1/task-context")
def get_task_context(
    request: Request,
    family_key: str = "",
    task_family: str = "",
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
    """Return one bounded policy + observed-procedure context surface.

    Resolution never uses arbitrary free text. Declared policy remains a separate
    normative input; observed procedure remains derived evidence. The endpoint is
    read-only and never executes or enforces a workflow action.
    """
    _require_api_read_bearer(request)
    try:
        raw = load_recent_evidence(limit=max(1, min(int(limit), 25_000)), since=since)
        manifest = load_declared_policy_manifest()
        payload = build_task_context(
            raw,
            family_key=family_key,
            task_family=task_family,
            current_steps=_steps(current_steps),
            after_step=after_step,
            min_support=min_support,
            run_limit=run_limit,
            section_limit=section_limit,
            max_steps_per_run=max_steps_per_run,
            max_evidence_refs_per_item=max_evidence_refs_per_item,
            manifest=manifest,
        )
    except (DeclaredPolicyError, TaskContextError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid task-context query") from exc

    return _strip_internal_ids({
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "policy_manifest_write_api_available": False,
    })
