from __future__ import annotations

"""Outcome tracking switch and status (see server/outcome_tracker.py)."""

from typing import Any

from pydantic import BaseModel

from . import outcome_tracker
from .secure_app import app


class OutcomeTrackingChoice(BaseModel):
    enabled: bool


@app.get("/v1/outcome-tracking")
def get_outcome_tracking() -> dict[str, Any]:
    return outcome_tracker.status()


@app.put("/v1/outcome-tracking")
def set_outcome_tracking(request: OutcomeTrackingChoice) -> dict[str, Any]:
    result = outcome_tracker.set_enabled(request.enabled)
    return {"status": "saved", **result, **outcome_tracker.status()}


@app.post("/v1/outcome-tracking/check-now")
def check_outcomes_now() -> dict[str, Any]:
    return {"checked": outcome_tracker.poll_once(), **outcome_tracker.status()}


__all__ = ["get_outcome_tracking", "set_outcome_tracking"]
