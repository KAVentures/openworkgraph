from __future__ import annotations

"""Privacy-minimized linkage from a shadow preview to a tool-call event."""

import re
from typing import Any, Mapping

from .action_guard import ActionGuardError
from .enforcement_preview import EnforcementPreview, SHADOW_PROFILE_ID


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FAMILY_KEY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_ALLOWED_DISPOSITIONS = frozenset({
    "candidate_deny",
    "candidate_pause_for_human_approval",
    "candidate_pause_for_prerequisite",
    "candidate_warn",
    "no_declared_enforcement_decision",
    "no_blocking_condition_observed",
    "indeterminate_policy_unavailable",
})


def shadow_enforcement_link(preview: EnforcementPreview) -> dict[str, Any]:
    """Project one #67 preview into the strict structural evidence shape."""
    if not isinstance(preview, EnforcementPreview):
        raise ActionGuardError("preview must be an EnforcementPreview")
    if preview.shadow_profile_id != SHADOW_PROFILE_ID:
        raise ActionGuardError("unsupported shadow preview profile")
    if preview.simulated_only is not True or preview.actual_enforcement_enabled is not False or preview.actual_blocking is not False:
        raise ActionGuardError("shadow preview is not simulation-only")

    family = str(preview.family_key or "").strip().lower()
    if not _FAMILY_KEY_RE.fullmatch(family):
        raise ActionGuardError("shadow preview contains invalid family_key")
    disposition = str(preview.candidate_disposition or "").strip().lower()
    if disposition not in _ALLOWED_DISPOSITIONS:
        raise ActionGuardError("shadow preview contains invalid candidate disposition")
    if preview.available and disposition == "indeterminate_policy_unavailable":
        raise ActionGuardError("available shadow preview cannot be indeterminate")
    if not preview.available and disposition != "indeterminate_policy_unavailable":
        raise ActionGuardError("unavailable shadow preview must be indeterminate")

    policy_sha = str(preview.manifest_sha256 or "").strip().lower()
    if policy_sha and not _SHA256_RE.fullmatch(policy_sha):
        raise ActionGuardError("shadow preview contains invalid policy manifest hash")

    return {
        "profile_id": SHADOW_PROFILE_ID,
        "available": bool(preview.available),
        "candidate_disposition": disposition,
        "family_key": family,
        "policy_manifest_sha256": policy_sha,
        "simulated_only": True,
        "actual_enforcement_enabled": False,
        "actual_blocking": False,
    }


def attach_shadow_preview_to_tool_call(
    event: Mapping[str, Any],
    preview: EnforcementPreview,
) -> dict[str, Any]:
    """Return a copy of a tool_call event carrying structural shadow telemetry."""
    if not isinstance(event, Mapping):
        raise ActionGuardError("agent event must be a mapping")
    if str(event.get("operation") or "").strip().lower() != "tool_call":
        raise ActionGuardError("shadow preview can only be attached to a tool_call event")
    if "shadow_enforcement" in event:
        raise ActionGuardError("agent event already contains shadow_enforcement telemetry")
    out = dict(event)
    out["shadow_enforcement"] = shadow_enforcement_link(preview)
    return out
