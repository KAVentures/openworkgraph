from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .context_outcome_associations import context_outcome_associations
from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence


router = APIRouter()


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.get("/v1/task-context/outcome-associations")
def get_context_outcome_associations(
    request: Request,
    family_key: str = "",
    since: str | None = None,
    evidence_limit: int = 25_000,
    min_group_support: int = 5,
    min_known_outcomes: int = 3,
    min_stratum_support: int = 2,
    max_strata: int = 50,
) -> dict[str, Any]:
    """Return aggregate, non-causal context/outcome associations."""
    _require_api_read_bearer(request)
    try:
        raw = load_recent_evidence(
            limit=max(1, min(int(evidence_limit), 100_000)),
            since=since,
        )
        payload = context_outcome_associations(
            raw,
            family_key=family_key,
            min_group_support=min_group_support,
            min_known_outcomes=min_known_outcomes,
            min_stratum_support=min_stratum_support,
            max_strata=max_strata,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid context-outcome query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "read_only": True,
        "writes_performed": False,
    }
