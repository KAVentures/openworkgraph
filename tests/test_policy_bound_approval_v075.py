from __future__ import annotations

from dataclasses import replace

import pytest

from adapters.action_guard import ActionPolicyClient
from adapters.policy_bound_approval import (
    ApprovalReceiptError,
    PolicyBoundApprovalGuard,
    issue_policy_bound_receipt,
    verify_policy_bound_receipt,
)


FAMILY = "human:github.create_issue"
DEPLOY = "tool:deployment:tool:aaaaaaaaaaaa"
SEARCH = "tool:search:tool:bbbbbbbbbbbb"
MANIFEST_V3 = "3" * 64
MANIFEST_V4 = "4" * 64


def _payload(
    body: dict,
    *,
    approval_missing: bool,
    active: bool = True,
    policy_id: str = "change-policy",
    version: str = "3",
    manifest: str = MANIFEST_V3,
) -> dict:
    if active:
        relevant = [{
            "rule_id": "approval-before-deploy",
            "type": "required_predecessor",
            "advisory": "approval_prerequisite_missing" if approval_missing else "prerequisite_satisfied",
            "warning": approval_missing,
            "constraint_satisfied": not approval_missing,
            "required_before": "approval_received:success",
            "trigger_step": body["proposed_step"],
            "approval_related": True,
        }]
    else:
        relevant = []
    return {
        "family_key": body["family_key"],
        "proposed_step": body["proposed_step"],
        "completed_steps_considered": list(body.get("completed_steps") or []),
        "declared_policy_status": "active" if active else "not_declared",
        "advisory_status": (
            "declared_policy_warning"
            if active and approval_missing
            else "matched_prerequisites_satisfied"
            if active
            else "no_active_declared_policy"
        ),
        "relevant_rules": relevant,
        "warning_count": 1 if active and approval_missing else 0,
        "missing_prerequisite_count": 1 if active and approval_missing else 0,
        "approval_prerequisite_missing": bool(active and approval_missing),
        "authorization_decision": "not_made",
        "action_allowed": None,
        "blocking": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "observed_work_used_as_permission": False,
        "observed_behavior_considered": False,
        "declared_policy": ({
            "policy_id": policy_id,
            "version": version,
            "family_key": body["family_key"],
            "source_type": "manual_sop",
            "source_ref_hash": "source:1234567890abcdef",
            "manifest_sha256": manifest,
            "declared": True,
            "policy_inferred": False,
        } if active else None),
        "manifest_present": active,
        "manifest_sha256": manifest if active else None,
        "derived": True,
        "read_only": True,
        "writes_performed": False,
        "capability_scope": "policy_action_advisory_only",
    }


def _stable_fetcher(calls: list[dict] | None = None):
    def fetcher(body):
        if calls is not None:
            calls.append(dict(body))
        approved = "approval_received:success" in (body.get("completed_steps") or [])
        return _payload(body, approval_missing=not approved)
    return fetcher


def test_unchanged_policy_snapshot_executes_and_returns_short_lived_receipt():
    calls: list[dict] = []
    clock = [1000.0]
    actions = []
    guard = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=_stable_fetcher(calls)),
        human_approval=lambda request: True,
        receipt_ttl_seconds=120,
        clock=lambda: clock[0],
    )
    result = guard.run(
        lambda: actions.append("ran") or "secret-result",
        family_key=FAMILY,
        proposed_step=DEPLOY,
        completed_steps=[SEARCH],
    )
    assert result.executed is True
    assert result.value == "secret-result"
    assert actions == ["ran"]
    assert result.decision.status == "approval_receipt_verified"
    assert result.decision.execute is True
    assert result.decision.receipt is not None
    assert result.decision.receipt.policy_version == "3"
    assert result.decision.receipt.manifest_sha256 == MANIFEST_V3
    assert result.decision.receipt.expires_at_unix == 1120.0
    assert result.decision.final_advisory is not None
    # #65 initial read + #65 post-approval recheck + #66 final fresh read.
    assert len(calls) == 3
    assert "approval_received:success" in calls[-1]["completed_steps"]
    # Caller-local action output never enters receipt/decision metadata.
    assert "secret-result" not in str(result.decision.as_dict())


def test_policy_version_change_after_human_approval_invalidates_receipt():
    calls = 0

    def fetcher(body):
        nonlocal calls
        calls += 1
        approved = "approval_received:success" in (body.get("completed_steps") or [])
        if calls == 1:
            return _payload(body, approval_missing=True, version="3", manifest=MANIFEST_V3)
        return _payload(body, approval_missing=not approved, version="4", manifest=MANIFEST_V4)

    actions = []
    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
    ).run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert actions == []
    assert result.decision.status == "paused_stale_approval_receipt"
    assert "policy_version_changed_after_approval" in result.decision.reason_codes


def test_same_policy_version_with_changed_manifest_is_treated_as_equivocation():
    calls = 0

    def fetcher(body):
        nonlocal calls
        calls += 1
        approved = "approval_received:success" in (body.get("completed_steps") or [])
        manifest = MANIFEST_V3 if calls == 1 else MANIFEST_V4
        return _payload(body, approval_missing=not approved, version="3", manifest=manifest)

    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
    ).run(lambda: pytest.fail("stale approval must not execute"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert "policy_manifest_changed_after_approval" in result.decision.reason_codes


def test_policy_removal_after_approval_requires_fresh_decision():
    calls = 0

    def fetcher(body):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _payload(body, approval_missing=True)
        return _payload(body, approval_missing=False, active=False)

    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
    ).run(lambda: pytest.fail("removed policy must not reuse prior approval"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert "declared_policy_removed_after_approval" in result.decision.reason_codes


def test_change_between_first_recheck_and_final_fresh_read_is_caught():
    calls = 0

    def fetcher(body):
        nonlocal calls
        calls += 1
        approved = "approval_received:success" in (body.get("completed_steps") or [])
        if calls <= 2:
            return _payload(body, approval_missing=not approved, version="3", manifest=MANIFEST_V3)
        return _payload(body, approval_missing=False, version="4", manifest=MANIFEST_V4)

    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
    ).run(lambda: pytest.fail("policy changed immediately before action"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert len(result.decision.reason_codes) >= 2
    assert "policy_version_changed_after_approval" in result.decision.reason_codes


def test_receipt_expiry_is_fail_safe():
    calls = 0
    clock = [1000.0]

    def fetcher(body):
        nonlocal calls
        calls += 1
        approved = "approval_received:success" in (body.get("completed_steps") or [])
        # Receipt is issued after #65's second read; expire it during #66's final read.
        if calls == 3:
            clock[0] = 1002.0
        return _payload(body, approval_missing=not approved)

    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
        receipt_ttl_seconds=1,
        clock=lambda: clock[0],
    ).run(lambda: pytest.fail("expired receipt must not execute"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert "approval_receipt_expired" in result.decision.reason_codes


def test_no_approval_required_preserves_existing_gate_behavior_without_receipt():
    calls = []

    def fetcher(body):
        calls.append(dict(body))
        return _payload(body, approval_missing=False)

    actions = []
    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: pytest.fail("approval should not be requested"),
    ).run(lambda: actions.append("ran") or 7, family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is True
    assert result.value == 7
    assert actions == ["ran"]
    assert result.decision.receipt is None
    assert result.decision.status == "proceed"
    assert len(calls) == 1


def test_human_denial_is_preserved_without_receipt_or_extra_policy_read():
    calls = []

    def fetcher(body):
        calls.append(dict(body))
        return _payload(body, approval_missing=True)

    result = PolicyBoundApprovalGuard(
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: False,
    ).run(lambda: pytest.fail("denied action must not run"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert result.decision.status == "approval_denied"
    assert result.decision.receipt is None
    assert len(calls) == 1


def test_receipt_verifier_rejects_wrong_action_and_expiry():
    client = ActionPolicyClient(fetcher=lambda body: _payload(body, approval_missing=True))
    advisory = client.advisory(family_key=FAMILY, proposed_step=DEPLOY)
    receipt = issue_policy_bound_receipt(advisory, ttl_seconds=10, now=100.0)

    ok, code = verify_policy_bound_receipt(receipt, advisory, now=105.0)
    assert ok is True and code == "approval_receipt_valid"

    wrong = replace(advisory, proposed_step=SEARCH)
    ok, code = verify_policy_bound_receipt(receipt, wrong, now=105.0)
    assert ok is False and code == "approval_receipt_action_mismatch"

    ok, code = verify_policy_bound_receipt(receipt, advisory, now=111.0)
    assert ok is False and code == "approval_receipt_expired"


def test_receipt_requires_active_bindable_policy_and_bounded_ttl():
    inactive_client = ActionPolicyClient(fetcher=lambda body: _payload(body, approval_missing=False, active=False))
    inactive = inactive_client.advisory(family_key=FAMILY, proposed_step=DEPLOY)
    with pytest.raises(ApprovalReceiptError):
        issue_policy_bound_receipt(inactive)

    active_client = ActionPolicyClient(fetcher=lambda body: _payload(body, approval_missing=True))
    active = active_client.advisory(family_key=FAMILY, proposed_step=DEPLOY)
    with pytest.raises(ApprovalReceiptError):
        issue_policy_bound_receipt(active, ttl_seconds=0)
    with pytest.raises(ApprovalReceiptError):
        issue_policy_bound_receipt(active, ttl_seconds=901)
