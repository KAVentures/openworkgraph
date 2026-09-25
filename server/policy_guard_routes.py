from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from .declared_policy import DeclaredPolicyError
from .policy_action_advisory import action_policy_advisory
from .policy_action_routes import ActionPolicyAdvisoryRequest
from .policy_guard_auth import policy_guard_bearer_matches


router = APIRouter()
POLICY_GUARD_ACTION_PATH = "/policy-guard/v1/action-advisory"


def _require_policy_guard_bearer(request: Request) -> None:
    if not policy_guard_bearer_matches(request.headers.get("authorization")):
        raise HTTPException(status_code=401, detail="policy guard authentication required")


@router.post(POLICY_GUARD_ACTION_PATH)
def policy_guard_action_advisory(
    body: ActionPolicyAdvisoryRequest,
    request: Request,
) -> dict[str, Any]:
    """Return one least-privilege structural policy advisory.

    This route deliberately sits outside ``/v1/`` so the secure-app middleware
    does not require the broader API/dashboard credential. Route-level auth is a
    dedicated read-only capability that is valid only for this structural query.
    It exposes no observed workflow history and performs no execution or writes.
    """
    _require_policy_guard_bearer(request)
    try:
        payload = action_policy_advisory(
            family_key=body.family_key,
            proposed_step=body.proposed_step,
            completed_steps=body.completed_steps,
        )
    except (DeclaredPolicyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid policy guard advisory query") from exc
    return {
        **payload,
        "read_only": True,
        "writes_performed": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "capability_scope": "policy_action_advisory_only",
    }
