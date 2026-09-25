from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .agent_execution_traces import agent_execution_traces
from .local_auth import bearer_matches
from .procedural_memory import load_recent_evidence


router = APIRouter()


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.get("/v1/agent-execution-traces")
def get_agent_execution_traces(
    request: Request,
    family_key: str = "",
    execution_id: str = "",
    since: str | None = None,
    evidence_limit: int = 25_000,
    limit: int = 20,
    max_events_per_execution: int = 100,
) -> dict[str, Any]:
    """Return privacy-safe ordered structural traces for observed agent runs."""
    _require_api_read_bearer(request)
    try:
        raw = load_recent_evidence(
            limit=max(1, min(int(evidence_limit), 100_000)),
            since=since,
        )
        payload = agent_execution_traces(
            raw,
            family_key=family_key,
            execution_id=execution_id,
            limit=limit,
            max_events_per_execution=max_events_per_execution,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid agent-execution-trace query") from exc
    return {
        **payload,
        "evidence_rows_considered": len(raw),
        "evidence_is_canonical": True,
        "read_only": True,
        "writes_performed": False,
    }
