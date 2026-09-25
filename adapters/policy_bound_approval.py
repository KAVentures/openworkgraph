from __future__ import annotations

"""Optional policy-snapshot binding for the #65 human approval gate.

This module is deliberately additive. Existing ``OptInActionGuard`` behavior is
unchanged. A runtime must explicitly choose ``PolicyBoundApprovalGuard`` to bind
a granted human approval to the exact declared-policy snapshot that was shown at
the time of approval.

The receipt is a local, short-lived structural object. It is not a bearer token,
not a signature, not persisted by OpenWorkGraph, and not proof of human identity.
Its purpose is narrower: detect policy/version/manifest changes between approval
and execution and require a fresh approval instead of silently reusing stale one.
"""

from dataclasses import dataclass
import hashlib
import json
import secrets
import time
from typing import Any, Callable, Generic, Iterable, Mapping, TypeVar

from .action_guard import (
    ActionGuardError,
    ActionPolicyAdvisory,
    ActionPolicyClient,
    ApprovalRequest,
    GateDecision,
    OptInActionGuard,
)
from .sdk import BufferedAgentEventSink


_MIN_TTL_SECONDS = 1.0
_MAX_TTL_SECONDS = 15 * 60.0
_DEFAULT_TTL_SECONDS = 2 * 60.0


class ApprovalReceiptError(ValueError):
    """The approval receipt cannot be issued or verified safely."""


def _bounded_ttl(value: float) -> float:
    try:
        ttl = float(value)
    except (TypeError, ValueError) as exc:
        raise ApprovalReceiptError("approval receipt ttl must be numeric") from exc
    if ttl < _MIN_TTL_SECONDS or ttl > _MAX_TTL_SECONDS:
        raise ApprovalReceiptError("approval receipt ttl must be between 1 and 900 seconds")
    return ttl


def _policy_identity(advisory: ActionPolicyAdvisory) -> tuple[str, str, str]:
    if not advisory.available or advisory.declared_policy_status != "active":
        raise ApprovalReceiptError("active declared policy is required for a bound approval receipt")
    policy_id = str(advisory.policy_id or "").strip()
    policy_version = str(advisory.policy_version or "").strip()
    manifest_sha256 = str(advisory.manifest_sha256 or "").strip().lower()
    if not policy_id or not policy_version or len(manifest_sha256) != 64:
        raise ApprovalReceiptError("declared policy snapshot is missing bindable identity metadata")
    try:
        int(manifest_sha256, 16)
    except ValueError as exc:
        raise ApprovalReceiptError("declared policy manifest fingerprint is invalid") from exc
    return policy_id, policy_version, manifest_sha256


def _receipt_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PolicyBoundApprovalReceipt:
    receipt_id: str
    family_key: str
    proposed_step: str
    policy_id: str
    policy_version: str
    manifest_sha256: str
    rule_ids: tuple[str, ...]
    issued_at_unix: float
    expires_at_unix: float
    fingerprint_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "receipt_id": self.receipt_id,
            "family_key": self.family_key,
            "proposed_step": self.proposed_step,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "manifest_sha256": self.manifest_sha256,
            "rule_ids": list(self.rule_ids),
            "issued_at_unix": self.issued_at_unix,
            "expires_at_unix": self.expires_at_unix,
            "fingerprint_sha256": self.fingerprint_sha256,
            "transferable_authorization": False,
            "cryptographic_human_attestation": False,
            "persisted_by_openworkgraph": False,
        }


def issue_policy_bound_receipt(
    advisory: ActionPolicyAdvisory,
    *,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> PolicyBoundApprovalReceipt:
    """Issue an in-memory receipt bound to one exact active policy snapshot."""
    if not advisory.approval_prerequisite_missing:
        raise ApprovalReceiptError("bound approval receipt requires a missing approval prerequisite")
    policy_id, policy_version, manifest_sha256 = _policy_identity(advisory)
    ttl = _bounded_ttl(ttl_seconds)
    issued = float(time.time() if now is None else now)
    expires = issued + ttl
    receipt_id = secrets.token_hex(16)
    structural = {
        "receipt_id": receipt_id,
        "family_key": advisory.family_key,
        "proposed_step": advisory.proposed_step,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "manifest_sha256": manifest_sha256,
        "rule_ids": sorted(str(item) for item in advisory.rule_ids),
        "issued_at_unix": issued,
        "expires_at_unix": expires,
    }
    return PolicyBoundApprovalReceipt(
        receipt_id=receipt_id,
        family_key=advisory.family_key,
        proposed_step=advisory.proposed_step,
        policy_id=policy_id,
        policy_version=policy_version,
        manifest_sha256=manifest_sha256,
        rule_ids=tuple(structural["rule_ids"]),
        issued_at_unix=issued,
        expires_at_unix=expires,
        fingerprint_sha256=_receipt_fingerprint(structural),
    )


def verify_policy_bound_receipt(
    receipt: PolicyBoundApprovalReceipt,
    advisory: ActionPolicyAdvisory,
    *,
    now: float | None = None,
) -> tuple[bool, str]:
    """Check that a receipt is fresh and still describes the current policy snapshot."""
    if not isinstance(receipt, PolicyBoundApprovalReceipt):
        return False, "approval_receipt_invalid"
    current = float(time.time() if now is None else now)
    if current > receipt.expires_at_unix:
        return False, "approval_receipt_expired"
    if not advisory.available:
        return False, "policy_unavailable_after_approval"
    if advisory.declared_policy_status != "active":
        return False, "declared_policy_removed_after_approval"
    if advisory.family_key != receipt.family_key or advisory.proposed_step != receipt.proposed_step:
        return False, "approval_receipt_action_mismatch"
    try:
        policy_id, policy_version, manifest_sha256 = _policy_identity(advisory)
    except ApprovalReceiptError:
        return False, "policy_snapshot_unbindable_after_approval"
    if policy_id != receipt.policy_id:
        return False, "policy_id_changed_after_approval"
    if policy_version != receipt.policy_version:
        return False, "policy_version_changed_after_approval"
    if manifest_sha256 != receipt.manifest_sha256:
        return False, "policy_manifest_changed_after_approval"
    return True, "approval_receipt_valid"


@dataclass(frozen=True)
class PolicyBoundGateDecision:
    execute: bool
    status: str
    reason_codes: tuple[str, ...]
    base_decision: GateDecision
    receipt: PolicyBoundApprovalReceipt | None = None
    final_advisory: ActionPolicyAdvisory | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "execute": self.execute,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "base_decision": self.base_decision.as_dict(),
            "receipt": self.receipt.as_dict() if self.receipt else None,
            "final_advisory": self.final_advisory.as_dict() if self.final_advisory else None,
            "policy_snapshot_binding": True,
            "hard_deny_enforcement": False,
        }


T = TypeVar("T")


@dataclass(frozen=True)
class PolicyBoundActionResult(Generic[T]):
    executed: bool
    decision: PolicyBoundGateDecision
    value: T | None = None


class PolicyBoundApprovalGuard:
    """Explicit higher-assurance wrapper around #65's approval gate.

    Existing ``OptInActionGuard`` remains unchanged. This wrapper delegates the
    initial advisory, human approval callback, structural approval telemetry and
    first post-approval recheck to #65, then adds receipt binding and a final fresh
    policy read immediately before action execution.
    """

    def __init__(
        self,
        *,
        client: ActionPolicyClient | None = None,
        human_approval: Callable[[ApprovalRequest], bool] | None = None,
        on_notice: Callable[[PolicyBoundGateDecision], None] | None = None,
        unavailable_behavior: str = "pause",
        receipt_ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
        event_sink: BufferedAgentEventSink | None = None,
        event_context: Mapping[str, Any] | None = None,
    ) -> None:
        self._client = client or ActionPolicyClient()
        self._receipt_ttl = _bounded_ttl(receipt_ttl_seconds)
        self._clock = clock or time.time
        self._on_notice = on_notice
        self._inner = OptInActionGuard(
            mode="approval_gate",
            client=self._client,
            human_approval=human_approval,
            on_notice=None,
            unavailable_behavior=unavailable_behavior,
            event_sink=event_sink,
            event_context=event_context,
        )

    def _notify(self, decision: PolicyBoundGateDecision) -> None:
        if self._on_notice is None:
            return
        try:
            self._on_notice(decision)
        except Exception:
            # UI/log notification is not part of the authorization boundary.
            pass

    @staticmethod
    def _approval_steps(advisory: ActionPolicyAdvisory) -> tuple[str, ...]:
        steps = list(advisory.completed_steps)
        for step in ("approval_request", "approval_received:success"):
            if step not in steps:
                steps.append(step)
        return tuple(steps)

    def evaluate(
        self,
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> PolicyBoundGateDecision:
        base = self._inner.evaluate(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )

        # If no human approval was granted, preserve #65's exact execution choice.
        # This includes ordinary no-approval-needed actions and all paused/denied
        # states. Receipt logic is intentionally scoped only to granted approvals.
        if base.human_approved is not True:
            decision = PolicyBoundGateDecision(
                execute=base.execute,
                status=base.status,
                reason_codes=base.reason_codes,
                base_decision=base,
            )
            self._notify(decision)
            return decision

        try:
            receipt = issue_policy_bound_receipt(
                base.advisory,
                ttl_seconds=self._receipt_ttl,
                now=self._clock(),
            )
        except ApprovalReceiptError:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_policy_snapshot_unbindable",
                reason_codes=("human_approval_granted", "policy_snapshot_unbindable"),
                base_decision=base,
            )
            self._notify(decision)
            return decision

        rechecked = base.rechecked_advisory
        if rechecked is None:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_missing_policy_recheck",
                reason_codes=("human_approval_granted", "policy_recheck_missing"),
                base_decision=base,
                receipt=receipt,
            )
            self._notify(decision)
            return decision

        valid, code = verify_policy_bound_receipt(receipt, rechecked, now=self._clock())
        if not valid:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_stale_approval_receipt",
                reason_codes=("human_approval_granted", code),
                base_decision=base,
                receipt=receipt,
                final_advisory=rechecked,
            )
            self._notify(decision)
            return decision
        if rechecked.approval_prerequisite_missing:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_approval_prerequisite_still_missing",
                reason_codes=("human_approval_granted", "approval_prerequisite_still_missing_after_recheck"),
                base_decision=base,
                receipt=receipt,
                final_advisory=rechecked,
            )
            self._notify(decision)
            return decision

        # Narrow the TOCTOU window with one fresh read immediately before run().
        final = self._client.try_advisory(
            family_key=receipt.family_key,
            proposed_step=receipt.proposed_step,
            completed_steps=self._approval_steps(base.advisory),
        )
        valid, code = verify_policy_bound_receipt(receipt, final, now=self._clock())
        if not valid:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_stale_approval_receipt",
                reason_codes=("human_approval_granted", code),
                base_decision=base,
                receipt=receipt,
                final_advisory=final,
            )
            self._notify(decision)
            return decision
        if final.approval_prerequisite_missing:
            decision = PolicyBoundGateDecision(
                execute=False,
                status="paused_approval_prerequisite_still_missing",
                reason_codes=("human_approval_granted", "approval_prerequisite_still_missing_before_execution"),
                base_decision=base,
                receipt=receipt,
                final_advisory=final,
            )
            self._notify(decision)
            return decision

        decision = PolicyBoundGateDecision(
            execute=bool(base.execute),
            status=("approval_receipt_verified" if base.execute else base.status),
            reason_codes=base.reason_codes + (("approval_receipt_verified",) if base.execute else ()),
            base_decision=base,
            receipt=receipt,
            final_advisory=final,
        )
        self._notify(decision)
        return decision

    def run(
        self,
        action: Callable[[], T],
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> PolicyBoundActionResult[T]:
        """Execute only if #65 passes and any granted approval remains snapshot-valid."""
        if not callable(action):
            raise ActionGuardError("action must be callable")
        decision = self.evaluate(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )
        if not decision.execute:
            return PolicyBoundActionResult(executed=False, decision=decision, value=None)
        value = action()
        return PolicyBoundActionResult(executed=True, decision=decision, value=value)
