from __future__ import annotations

"""Opt-in shadow enforcement preview for declared structural policy.

This module never enforces policy. It maps the existing action-policy advisory
into a hypothetical strict preview profile so an integration can observe what a
future enforcement layer *would* have done while the wrapped action still runs.

Nothing imports or activates this module automatically.
"""

from dataclasses import dataclass
from typing import Any, Callable, Generic, Iterable, TypeVar

from .action_guard import ActionPolicyAdvisory, ActionPolicyClient


SHADOW_PROFILE_ID = "declared-policy-shadow-v1"


@dataclass(frozen=True)
class EnforcementPreview:
    available: bool
    family_key: str
    proposed_step: str
    completed_steps: tuple[str, ...]
    shadow_profile_id: str
    candidate_disposition: str
    reason_codes: tuple[str, ...]
    policy_id: str | None
    policy_version: str | None
    manifest_sha256: str | None
    rule_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    simulated_only: bool = True
    actual_enforcement_enabled: bool = False
    actual_blocking: bool = False
    execution_performed: bool = False
    authorization_decision: str = "not_made"
    action_allowed: None = None

    @classmethod
    def from_advisory(cls, advisory: ActionPolicyAdvisory) -> "EnforcementPreview":
        disposition, reasons = _candidate_disposition(advisory)
        return cls(
            available=advisory.available,
            family_key=advisory.family_key,
            proposed_step=advisory.proposed_step,
            completed_steps=advisory.completed_steps,
            shadow_profile_id=SHADOW_PROFILE_ID,
            candidate_disposition=disposition,
            reason_codes=reasons,
            policy_id=advisory.policy_id,
            policy_version=advisory.policy_version,
            manifest_sha256=advisory.manifest_sha256,
            rule_ids=advisory.rule_ids,
            warning_codes=advisory.warning_codes,
        )

    @property
    def candidate_would_interrupt(self) -> bool | None:
        if self.candidate_disposition in {
            "candidate_deny",
            "candidate_pause_for_human_approval",
            "candidate_pause_for_prerequisite",
        }:
            return True
        if self.candidate_disposition == "no_blocking_condition_observed":
            return False
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "family_key": self.family_key,
            "proposed_step": self.proposed_step,
            "completed_steps": list(self.completed_steps),
            "shadow_profile_id": self.shadow_profile_id,
            "candidate_disposition": self.candidate_disposition,
            "candidate_would_interrupt": self.candidate_would_interrupt,
            "reason_codes": list(self.reason_codes),
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "manifest_sha256": self.manifest_sha256,
            "rule_ids": list(self.rule_ids),
            "warning_codes": list(self.warning_codes),
            "simulated_only": self.simulated_only,
            "actual_enforcement_enabled": self.actual_enforcement_enabled,
            "actual_blocking": self.actual_blocking,
            "execution_performed": self.execution_performed,
            "authorization_decision": self.authorization_decision,
            "action_allowed": self.action_allowed,
        }


def _candidate_disposition(advisory: ActionPolicyAdvisory) -> tuple[str, tuple[str, ...]]:
    """Map declared-policy advisory state into the fixed shadow profile.

    The mapping is intentionally descriptive and non-authoritative. It does not
    grant permission. In particular, no policy/no matching rule remain
    ``no_declared_enforcement_decision`` rather than becoming "allow".
    """
    if not advisory.available:
        return "indeterminate_policy_unavailable", ("policy_guard_unavailable",)

    if advisory.forbidden_step_warning:
        return "candidate_deny", ("declared_forbidden_step",)

    if advisory.approval_prerequisite_missing:
        return "candidate_pause_for_human_approval", ("approval_prerequisite_missing",)

    if advisory.nonapproval_prerequisite_missing:
        return "candidate_pause_for_prerequisite", ("prerequisite_missing",)

    if advisory.warning_count:
        return "candidate_warn", tuple(advisory.warning_codes or ("policy_warning",))

    if advisory.declared_policy_status != "active":
        return "no_declared_enforcement_decision", ("no_active_declared_policy",)

    if advisory.advisory_status == "no_matching_declared_constraint":
        return "no_declared_enforcement_decision", ("no_matching_declared_constraint",)

    return "no_blocking_condition_observed", ("declared_constraints_currently_satisfied",)


class ShadowEnforcementSimulator:
    """Read-only hypothetical enforcement classifier.

    ``evaluate`` only returns a preview. ``run`` additionally invokes the caller's
    zero-argument action regardless of the preview disposition. Service
    unavailability therefore remains non-blocking in shadow mode. Invalid caller
    input or malformed advisory responses still raise before execution because
    those are integration errors, not policy decisions.
    """

    def __init__(
        self,
        *,
        client: ActionPolicyClient | None = None,
        on_preview: Callable[[EnforcementPreview], None] | None = None,
    ) -> None:
        self._client = client or ActionPolicyClient()
        self._on_preview = on_preview

    def _notify(self, preview: EnforcementPreview) -> None:
        if self._on_preview is None:
            return
        try:
            self._on_preview(preview)
        except Exception:
            # Reporting/UX must not turn a shadow preview into execution control.
            pass

    def evaluate(
        self,
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> EnforcementPreview:
        advisory = self._client.try_advisory(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )
        preview = EnforcementPreview.from_advisory(advisory)
        self._notify(preview)
        return preview

    def run(
        self,
        action: Callable[[], "T"],
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> "ShadowActionResult[T]":
        if not callable(action):
            raise TypeError("action must be callable")
        preview = self.evaluate(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )
        value = action()
        return ShadowActionResult(executed=True, preview=preview, value=value)


T = TypeVar("T")


@dataclass(frozen=True)
class ShadowActionResult(Generic[T]):
    executed: bool
    preview: EnforcementPreview
    value: T | None = None
