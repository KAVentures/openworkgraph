from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence
from .shadow_enforcement_associations import shadow_enforcement_outcome_associations


router = APIRouter()


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.get("/v1/shadow-enforcement/outcome-associations")
def get_shadow_enforcement_outcome_associations(
    request: Request,
    family_key: str = "",
    since: str | None = None,
    evidence_limit: int = 25_000,
    min_stratum_support: int = 2,
    max_strata: int = 50,
) -> dict[str, Any]:
    """Return aggregate, non-causal shadow-preview/run-outcome associations."""
    _require_api_read_bearer(request)
    try:
        raw = load_recent_evidence(
            limit=max(1, min(int(evidence_limit), 100_000)),
            since=since,
        )
        payload = shadow_enforcement_outcome_associations(
            raw,
            family_key=family_key,
            min_stratum_support=min_stratum_support,
            max_strata=max_strata,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid shadow-enforcement analytics query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "read_only": True,
        "writes_performed": False,
    }
