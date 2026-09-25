from __future__ import annotations

"""Explicit adapter assertions for verified task-context handoff delivery.

These helpers only describe what the integration itself did with an already
verified :class:`AgentContextHandoff`. They never attest that a model read,
understood, used, followed, or complied with the delivered context.
"""

from typing import Any, Mapping

from .context_handoff import AgentContextHandoff
from .task_preflight import TaskPreflightError


_HANDOFF_STATUSES = frozenset({"prepared", "delivered_to_runtime"})


def context_delivery_link(
    handoff: AgentContextHandoff,
    *,
    status: str,
) -> dict[str, Any]:
    """Return strict run-start linkage plus one adapter-reported handoff status."""
    if not isinstance(handoff, AgentContextHandoff):
        raise TaskPreflightError("handoff must be an AgentContextHandoff")
    normalized = str(status or "").strip().lower()
    if normalized not in _HANDOFF_STATUSES:
        raise TaskPreflightError("invalid task-context handoff status")
    if not handoff.available:
        raise TaskPreflightError("cannot assert handoff preparation or delivery for unavailable context")

    link = handoff.linkage_dict()
    if link.get("available") is not True:
        raise TaskPreflightError("available handoff has contradictory run-start linkage")
    if str(link.get("context_sha256") or "").strip().lower() != str(handoff.context_sha256 or "").strip().lower():
        raise TaskPreflightError("handoff fingerprint does not match run-start linkage")

    link["handoff_status"] = normalized
    return link


def attach_context_delivery_to_run_started(
    event: Mapping[str, Any],
    handoff: AgentContextHandoff,
    *,
    status: str,
) -> dict[str, Any]:
    """Attach one explicit preparation/delivery assertion to a run-start event.

    Callers should use ``delivered_to_runtime`` only after their own runtime
    interface has accepted the verified handoff envelope. The assertion still
    says nothing about whether a model consumed or acted on that context.
    """
    if not isinstance(event, Mapping):
        raise TaskPreflightError("agent event must be a mapping")
    if str(event.get("operation") or "").strip().lower() != "run_started":
        raise TaskPreflightError("task-context handoff delivery can only be attached to run_started")
    if "task_context" in event:
        raise TaskPreflightError("agent event already contains task_context linkage")

    out = dict(event)
    out["task_context"] = context_delivery_link(handoff, status=status)
    return out
