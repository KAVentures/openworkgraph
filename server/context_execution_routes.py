from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .context_execution_linkage import context_execution_linkage
from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence


router = APIRouter()


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.get("/v1/task-context/executions")
def get_context_execution_linkage(
    request: Request,
    family_key: str = "",
    since: str | None = None,
    evidence_limit: int = 25_000,
    limit: int = 50,
    include_unlinked: bool = True,
) -> dict[str, Any]:
    """Return privacy-safe structural linkage between preflight context and runs."""
    _require_api_read_bearer(request)
    try:
        raw = load_recent_evidence(
            limit=max(1, min(int(evidence_limit), 100_000)),
            since=since,
        )
        payload = context_execution_linkage(
            raw,
            family_key=family_key,
            limit=limit,
            include_unlinked=bool(include_unlinked),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid context-execution query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "read_only": True,
        "writes_performed": False,
    }
