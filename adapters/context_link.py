from __future__ import annotations

"""Structural task-context linkage helpers for agent run-start events.

The helpers intentionally carry only hashes, booleans and a validated structural
family identifier. They never attach the task-context payload itself, prompts,
model output, tool arguments/results, or reasoning.
"""

import re
from typing import Any, Mapping

from .task_preflight import TaskPreflight, TaskPreflightError


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")


def task_context_link(preflight: TaskPreflight) -> dict[str, Any]:
    """Project one preflight result into the strict agent-evidence linkage shape."""
    if not isinstance(preflight, TaskPreflight):
        raise TaskPreflightError("preflight must be a TaskPreflight result")

    out: dict[str, Any] = {
        "preflight_attempted": True,
        "available": bool(preflight.available),
        "resolved": bool(preflight.context_resolved),
    }
    if not preflight.available:
        if preflight.context_resolved or preflight.context_sha256 or preflight.policy_manifest_sha256 or preflight.family_key:
            raise TaskPreflightError("unavailable preflight contains contradictory linkage metadata")
        return out

    context_sha = str(preflight.context_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(context_sha):
        raise TaskPreflightError("available preflight is missing a valid context fingerprint")
    out["context_sha256"] = context_sha

    policy_sha = str(preflight.policy_manifest_sha256 or "").strip().lower()
    if policy_sha:
        if not _SHA256_RE.fullmatch(policy_sha):
            raise TaskPreflightError("preflight contains an invalid policy manifest fingerprint")
        out["policy_manifest_sha256"] = policy_sha

    family_key = str(preflight.family_key or "").strip().lower()
    if preflight.context_resolved:
        if not family_key or not _FAMILY_KEY_RE.fullmatch(family_key):
            raise TaskPreflightError("resolved preflight is missing a valid family key")
        out["family_key"] = family_key
    elif family_key:
        # A resolver should not claim a selected family when context was not resolved.
        raise TaskPreflightError("unresolved preflight unexpectedly contains a family key")
    return out


def attach_preflight_to_run_started(
    event: Mapping[str, Any],
    preflight: TaskPreflight,
) -> dict[str, Any]:
    """Return a copy of a run_started event with structural context linkage attached."""
    if not isinstance(event, Mapping):
        raise TaskPreflightError("agent event must be a mapping")
    if str(event.get("operation") or "").strip().lower() != "run_started":
        raise TaskPreflightError("task preflight can only be attached to a run_started event")
    if "task_context" in event:
        raise TaskPreflightError("agent event already contains task_context linkage")
    out = dict(event)
    out["task_context"] = task_context_link(preflight)
    return out
