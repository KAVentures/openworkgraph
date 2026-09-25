from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .declared_policy import DeclaredPolicyError
from .local_auth import bearer_matches
from .policy_action_advisory import action_policy_advisory


router = APIRouter()


class ActionPolicyAdvisoryRequest(BaseModel):
    family_key: str = Field(min_length=1, max_length=200)
    proposed_step: str = Field(min_length=1, max_length=200)
    completed_steps: list[str] = Field(default_factory=list, max_length=48)


def _require_api_read_bearer(request: Request) -> None:
    if not bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="API authentication required")


@router.post("/v1/declared-policies/action-advisory")
def get_action_policy_advisory(
    body: ActionPolicyAdvisoryRequest,
    request: Request,
) -> dict[str, Any]:
    """Return declared-policy advice for one proposed structural step.

    POST is used only to carry a bounded structured query. This endpoint performs
    no writes, does not execute or block the action, and does not infer policy
    from observed workflow behavior.
    """
    _require_api_read_bearer(request)
    try:
        payload = action_policy_advisory(
            family_key=body.family_key,
            proposed_step=body.proposed_step,
            completed_steps=body.completed_steps,
        )
    except (DeclaredPolicyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid policy action advisory query") from exc
    return {
        **payload,
        "read_only": True,
        "writes_performed": False,
        "automatic_enforcement": False,
        "execution_performed": False,
    }
