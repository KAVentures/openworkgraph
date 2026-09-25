from __future__ import annotations

import pytest

from adapters.action_guard import (
    ActionGuardError,
    ActionGuardUnavailable,
    ActionPolicyClient,
    OptInActionGuard,
)


FAMILY = "human:github.create_issue"
DEPLOY = "tool:deployment:tool:aaaaaaaaaaaa"
SEARCH = "tool:search:tool:bbbbbbbbbbbb"
MANIFEST = "c" * 64


def _payload(
    body: dict,
    *,
    warning_codes: tuple[str, ...] = (),
    approval_missing: bool = False,
    active: bool = True,
) -> dict:
    relevant = []
    for index, code in enumerate(warning_codes):
        item = {
            "rule_id": f"rule-{index}",
            "type": "forbidden_step" if code == "declared_forbidden_step" else "required_predecessor",
            "advisory": code,
            "warning": True,
        }
        if code in {"approval_prerequisite_missing", "prerequisite_missing"}:
            item.update({"required_before": "approval_received:success", "trigger_step": body["proposed_step"]})
        relevant.append(item)
    if active and not warning_codes:
        relevant = [{
            "rule_id": "approval-before-deploy",
            "type": "required_predecessor",
            "advisory": "prerequisite_satisfied",
            "warning": False,
            "constraint_satisfied": True,
            "required_before": "approval_received:success",
            "trigger_step": body["proposed_step"],
            "approval_related": True,
        }]
    return {
        "family_key": body["family_key"],
        "proposed_step": body["proposed_step"],
        "completed_steps_considered": list(body.get("completed_steps") or []),
        "declared_policy_status": "active" if active else "not_declared",
        "advisory_status": (
            "declared_policy_warning"
            if warning_codes
            else "matched_prerequisites_satisfied"
            if active
            else "no_active_declared_policy"
        ),
        "relevant_rules": relevant,
        "warning_count": len(warning_codes),
        "missing_prerequisite_count": sum(code in {"approval_prerequisite_missing", "prerequisite_missing"} for code in warning_codes),
        "approval_prerequisite_missing": approval_missing,
        "authorization_decision": "not_made",
        "action_allowed": None,
        "blocking": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "observed_work_used_as_permission": False,
        "observed_behavior_considered": False,
        "declared_policy": ({
            "policy_id": "change-policy",
            "version": "3",
            "family_key": body["family_key"],
            "source_type": "manual_sop",
            "source_ref_hash": "source:1234567890abcdef",
            "manifest_sha256": MANIFEST,
            "declared": True,
            "policy_inferred": False,
        } if active else None),
        "manifest_present": active,
        "manifest_sha256": MANIFEST if active else None,
        "derived": True,
        "read_only": True,
        "writes_performed": False,
        "capability_scope": "policy_action_advisory_only",
    }


def test_warn_mode_surfaces_forbidden_warning_but_executes_wrapped_action():
    def fetcher(body):
        return _payload(body, warning_codes=("declared_forbidden_step",))

    notices = []
    actions = []
    guard = OptInActionGuard(
        mode="warn",
        client=ActionPolicyClient(fetcher=fetcher),
        on_notice=notices.append,
    )
    result = guard.run(
        lambda: actions.append("ran") or "value",
        family_key=FAMILY,
        proposed_step=DEPLOY,
    )
    assert result.executed is True
    assert result.value == "value"
    assert actions == ["ran"]
    assert result.decision.status == "proceed_with_warning"
    assert result.decision.execute is True
    assert result.decision.advisory.forbidden_step_warning is True
    assert notices and notices[0].status == "proceed_with_warning"


def test_approval_gate_without_handler_does_not_execute():
    def fetcher(body):
        return _payload(body, warning_codes=("approval_prerequisite_missing",), approval_missing=True)

    actions = []
    guard = OptInActionGuard(mode="approval_gate", client=ActionPolicyClient(fetcher=fetcher))
    result = guard.run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert actions == []
    assert result.decision.status == "paused_approval_handler_missing"
    assert result.decision.approval_requested is False


def test_human_denial_and_non_boolean_decision_never_execute():
    def fetcher(body):
        return _payload(body, warning_codes=("approval_prerequisite_missing",), approval_missing=True)

    denied_actions = []
    denied = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: False,
    ).run(lambda: denied_actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert denied.executed is False
    assert denied_actions == []
    assert denied.decision.status == "approval_denied"
    assert denied.decision.human_approved is False

    invalid_actions = []
    invalid = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: "yes",  # type: ignore[return-value]
    ).run(lambda: invalid_actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert invalid.executed is False
    assert invalid_actions == []
    assert invalid.decision.status == "paused_invalid_approval_decision"
    assert invalid.decision.human_approved is None


def test_human_approval_is_rechecked_before_action_executes():
    calls: list[dict] = []

    def fetcher(body):
        calls.append(dict(body))
        completed = body.get("completed_steps") or []
        if "approval_received:success" not in completed:
            return _payload(body, warning_codes=("approval_prerequisite_missing",), approval_missing=True)
        return _payload(body)

    approval_requests = []
    actions = []
    guard = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: approval_requests.append(request) is None or True,
    )
    result = guard.run(
        lambda: actions.append("ran") or {"secret_action_result": True},
        family_key=FAMILY,
        proposed_step=DEPLOY,
        completed_steps=[SEARCH],
    )
    assert result.executed is True
    assert actions == ["ran"]
    assert result.decision.status == "approval_granted"
    assert result.decision.human_approved is True
    assert len(calls) == 2
    assert calls[0]["completed_steps"] == [SEARCH]
    assert "approval_request" in calls[1]["completed_steps"]
    assert "approval_received:success" in calls[1]["completed_steps"]
    assert approval_requests[0].family_key == FAMILY
    assert approval_requests[0].proposed_step == DEPLOY
    # Action values remain caller-local and are not put into guard/advisory metadata.
    assert "secret_action_result" not in str(result.decision.as_dict())


def test_policy_recheck_still_missing_keeps_action_paused():
    def fetcher(body):
        return _payload(body, warning_codes=("approval_prerequisite_missing",), approval_missing=True)

    actions = []
    result = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
    ).run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is False
    assert actions == []
    assert result.decision.status == "paused_approval_prerequisite_still_missing"
    assert result.decision.human_approved is True


def test_unavailable_service_is_fail_open_for_warn_and_fail_safe_for_gate():
    def unavailable(body):
        raise ActionGuardUnavailable("down")

    warn_actions = []
    warn = OptInActionGuard(
        mode="warn",
        client=ActionPolicyClient(fetcher=unavailable),
    ).run(lambda: warn_actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert warn.executed is True
    assert warn.decision.status == "proceed_policy_unavailable"
    assert warn_actions == ["ran"]

    gated_actions = []
    gated = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=unavailable),
    ).run(lambda: gated_actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert gated.executed is False
    assert gated.decision.status == "paused_policy_unavailable"
    assert gated_actions == []

    explicit_actions = []
    explicit = OptInActionGuard(
        mode="approval_gate",
        unavailable_behavior="continue",
        client=ActionPolicyClient(fetcher=unavailable),
    ).run(lambda: explicit_actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert explicit.executed is True
    assert explicit.decision.status == "proceed_policy_unavailable"
    assert explicit_actions == ["ran"]


def test_malformed_authorization_response_and_instruction_like_input_fail_closed():
    def bad_fetcher(body):
        payload = _payload(body)
        payload["action_allowed"] = True
        return payload

    client = ActionPolicyClient(fetcher=bad_fetcher)
    with pytest.raises(ActionGuardError):
        client.advisory(family_key=FAMILY, proposed_step=DEPLOY)
    with pytest.raises(ActionGuardError):
        ActionPolicyClient(fetcher=lambda body: _payload(body)).advisory(
            family_key=FAMILY,
            proposed_step="ignore_previous_instructions",
        )


def test_approval_telemetry_is_structural_and_best_effort():
    def fetcher(body):
        if "approval_received:success" in (body.get("completed_steps") or []):
            return _payload(body)
        return _payload(body, warning_codes=("approval_prerequisite_missing",), approval_missing=True)

    class Sink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(dict(event))
            return True

    sink = Sink()
    guard = OptInActionGuard(
        mode="approval_gate",
        client=ActionPolicyClient(fetcher=fetcher),
        human_approval=lambda request: True,
        event_sink=sink,  # type: ignore[arg-type]
        event_context={
            "agent_name": "Guard Test Agent",
            "provider": "test",
            "framework": "custom",
            "run_id": "private-run-id",
        },
    )
    result = guard.run(lambda: "ok", family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is True
    assert [(e["operation"], e["status"]) for e in sink.events] == [
        ("human_approval_requested", "running"),
        ("human_approval_received", "success"),
    ]
    serialized = str(sink.events).lower()
    assert "prompt" not in serialized
    assert "tool_input" not in serialized
    assert "tool_result" not in serialized

    with pytest.raises(ActionGuardError):
        OptInActionGuard(
            mode="approval_gate",
            client=ActionPolicyClient(fetcher=fetcher),
            event_sink=sink,  # type: ignore[arg-type]
            event_context={
                "agent_name": "Guard Test Agent",
                "run_id": "r",
                "prompt": "secret",
            },
        )


def test_notice_callback_failure_cannot_change_gate_decision():
    def fetcher(body):
        return _payload(body, active=False)

    actions = []
    guard = OptInActionGuard(
        mode="warn",
        client=ActionPolicyClient(fetcher=fetcher),
        on_notice=lambda decision: (_ for _ in ()).throw(RuntimeError("ui failed")),
    )
    result = guard.run(lambda: actions.append("ran"), family_key=FAMILY, proposed_step=DEPLOY)
    assert result.executed is True
    assert actions == ["ran"]
