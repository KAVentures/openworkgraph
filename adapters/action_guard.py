from __future__ import annotations

"""Opt-in execution-path guard for explicit declared structural policy.

Nothing in OpenWorkGraph imports or activates this guard automatically. A runtime
must deliberately wrap an action with ``OptInActionGuard``. ``warn`` mode never
blocks because of a policy result. ``approval_gate`` pauses only when the existing
declared-policy advisory reports a missing approval prerequisite; it does not
turn observed workflow behavior into permission and it does not hard-enforce
other policy warnings in this slice.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import ipaddress
import json
import os
import re
import socket
from typing import Any, Callable, Generic, Iterable, Mapping, TypeVar
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from server.policy_guard_auth import ensure_policy_guard_token
from server.procedural_context_pack import _generated_structural_step
from shared.agent_evidence import AgentEvidenceError, agent_event_to_evidence

from .sdk import BufferedAgentEventSink


DEFAULT_API = "http://127.0.0.1:8787"
POLICY_GUARD_PATH = "/policy-guard/v1/action-advisory"
_MAX_RESPONSE_BYTES = 256_000
_MAX_COMPLETED_STEPS = 48
_FAMILY_RE = re.compile(r"^[a-z0-9:._-]{1,200}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ALLOWED_ADVISORY_STATUS = frozenset({
    "declared_policy_warning",
    "matched_prerequisites_satisfied",
    "declared_required_step_proposed",
    "no_matching_declared_constraint",
    "no_active_declared_policy",
})
_ALLOWED_RULE_ADVISORIES = frozenset({
    "declared_forbidden_step",
    "prerequisite_satisfied",
    "approval_prerequisite_missing",
    "prerequisite_missing",
    "declared_required_step_is_proposed",
})
_EVENT_CONTEXT_KEYS = frozenset({
    "agent_name",
    "provider",
    "framework",
    "model",
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "workflow_id",
    "organization_id",
    "actor_id",
    "device_id",
    "observation_level",
})
_FORBIDDEN_RESPONSE_KEYS = frozenset({
    "prompt", "prompts", "message", "messages", "completion", "completions",
    "content", "input", "output", "request", "response", "reasoning",
    "chain_of_thought", "chain-of-thought", "tool_arguments", "tool_args",
    "tool_input", "tool_result", "tool_results", "tool_output",
})


class ActionGuardError(ValueError):
    """Invalid guard configuration/request or malformed advisory response."""


class ActionGuardUnavailable(RuntimeError):
    """The configured policy-guard service could not provide an advisory."""


def _normalize_family(value: str) -> str:
    family = str(value or "").strip().lower()
    if not _FAMILY_RE.fullmatch(family):
        raise ActionGuardError("invalid structural family_key")
    return family


def _normalize_step(value: str, *, field: str) -> str:
    step = str(value or "").strip().lower()
    if not _generated_structural_step(step):
        raise ActionGuardError(f"invalid {field}")
    return step


def _normalize_steps(values: Iterable[str]) -> tuple[str, ...]:
    steps = tuple(str(item).strip().lower() for item in values if str(item).strip())
    if len(steps) > _MAX_COMPLETED_STEPS:
        raise ActionGuardError("too many completed structural steps")
    if any(not _generated_structural_step(step) for step in steps):
        raise ActionGuardError("invalid completed structural steps")
    return steps


def _assert_structural_response(value: Any, *, path: str = "advisory") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_RESPONSE_KEYS:
                raise ActionGuardError(f"content-bearing advisory field is not allowed: {path}.{key}")
            _assert_structural_response(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _assert_structural_response(child, path=f"{path}[{index}]")


def _is_loopback(hostname: str) -> bool:
    host = hostname.strip().lower()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _base_url() -> tuple[str, bool]:
    raw = os.getenv("WORKFLOW_OBSERVER_API", DEFAULT_API).strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ActionGuardError("invalid WORKFLOW_OBSERVER_API")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ActionGuardError("WORKFLOW_OBSERVER_API must not contain credentials, query, or fragment")
    local = _is_loopback(parsed.hostname)
    if not local and os.getenv("OWG_ACTION_GUARD_ALLOW_REMOTE", "").strip() != "1":
        raise ActionGuardError("remote policy guard requires OWG_ACTION_GUARD_ALLOW_REMOTE=1")
    return raw, local


def _policy_guard_token(*, local: bool) -> str:
    configured = os.getenv("OWG_POLICY_GUARD_TOKEN", "").strip()
    if configured:
        return configured
    if local:
        return ensure_policy_guard_token()
    raise ActionGuardError("remote policy guard requires OWG_POLICY_GUARD_TOKEN")


def _default_fetch(payload: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    base, local = _base_url()
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        base + POLICY_GUARD_PATH,
        data=body,
        headers={
            "Authorization": f"Bearer {_policy_guard_token(local=local)}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(0.05, min(float(timeout), 10.0))) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        code = int(exc.code)
        if code == 404 or code >= 500:
            raise ActionGuardUnavailable("policy guard service unavailable") from exc
        raise ActionGuardError("policy guard request rejected") from exc
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise ActionGuardUnavailable("policy guard service unavailable") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ActionGuardError("policy guard response exceeds client safety limit")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionGuardError("invalid policy guard response") from exc
    if not isinstance(value, dict):
        raise ActionGuardError("invalid policy guard response")
    return value


@dataclass(frozen=True)
class ActionPolicyAdvisory:
    available: bool
    family_key: str
    proposed_step: str
    completed_steps: tuple[str, ...]
    declared_policy_status: str
    advisory_status: str
    warning_count: int
    missing_prerequisite_count: int
    approval_prerequisite_missing: bool
    forbidden_step_warning: bool
    nonapproval_prerequisite_missing: bool
    policy_id: str | None
    policy_version: str | None
    manifest_sha256: str | None
    rule_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]
    error_code: str | None

    @classmethod
    def unavailable(
        cls,
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: tuple[str, ...],
    ) -> "ActionPolicyAdvisory":
        return cls(
            available=False,
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
            declared_policy_status="unknown",
            advisory_status="unavailable",
            warning_count=0,
            missing_prerequisite_count=0,
            approval_prerequisite_missing=False,
            forbidden_step_warning=False,
            nonapproval_prerequisite_missing=False,
            policy_id=None,
            policy_version=None,
            manifest_sha256=None,
            rule_ids=(),
            warning_codes=(),
            error_code="policy_guard_unavailable",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "family_key": self.family_key,
            "proposed_step": self.proposed_step,
            "completed_steps": list(self.completed_steps),
            "declared_policy_status": self.declared_policy_status,
            "advisory_status": self.advisory_status,
            "warning_count": self.warning_count,
            "missing_prerequisite_count": self.missing_prerequisite_count,
            "approval_prerequisite_missing": self.approval_prerequisite_missing,
            "forbidden_step_warning": self.forbidden_step_warning,
            "nonapproval_prerequisite_missing": self.nonapproval_prerequisite_missing,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "manifest_sha256": self.manifest_sha256,
            "rule_ids": list(self.rule_ids),
            "warning_codes": list(self.warning_codes),
            "error_code": self.error_code,
        }


def _nonnegative_int(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool):
        raise ActionGuardError("invalid policy guard response")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ActionGuardError("invalid policy guard response") from exc
    if parsed < 0 or parsed > 10_000:
        raise ActionGuardError("invalid policy guard response")
    return parsed


def _parse_advisory(
    payload: dict[str, Any],
    *,
    family_key: str,
    proposed_step: str,
    completed_steps: tuple[str, ...],
) -> ActionPolicyAdvisory:
    _assert_structural_response(payload)
    invariants = {
        "read_only": True,
        "writes_performed": False,
        "automatic_enforcement": False,
        "execution_performed": False,
        "blocking": False,
        "observed_work_used_as_permission": False,
        "observed_behavior_considered": False,
        "authorization_decision": "not_made",
        "capability_scope": "policy_action_advisory_only",
    }
    for key, expected in invariants.items():
        if payload.get(key) != expected:
            raise ActionGuardError("policy guard response violates the advisory-only contract")
    if payload.get("action_allowed") is not None:
        raise ActionGuardError("policy guard response unexpectedly contains an authorization decision")

    returned_family = str(payload.get("family_key") or "").strip().lower()
    returned_step = str(payload.get("proposed_step") or "").strip().lower()
    returned_completed = tuple(str(item).strip().lower() for item in (payload.get("completed_steps_considered") or []))
    if returned_family != family_key or returned_step != proposed_step or returned_completed != completed_steps:
        raise ActionGuardError("policy guard response does not match the requested structural action")

    advisory_status = str(payload.get("advisory_status") or "")
    if advisory_status not in _ALLOWED_ADVISORY_STATUS:
        raise ActionGuardError("invalid policy guard advisory status")
    policy_status = str(payload.get("declared_policy_status") or "")
    if policy_status not in {"active", "not_declared"}:
        raise ActionGuardError("invalid declared-policy status")

    relevant = payload.get("relevant_rules")
    if not isinstance(relevant, list) or len(relevant) > 64:
        raise ActionGuardError("invalid policy guard rule set")
    rule_ids: list[str] = []
    warning_codes: list[str] = []
    for item in relevant:
        if not isinstance(item, dict):
            raise ActionGuardError("invalid policy guard rule")
        code = str(item.get("advisory") or "")
        if code not in _ALLOWED_RULE_ADVISORIES:
            raise ActionGuardError("invalid policy guard rule advisory")
        rule_id = str(item.get("rule_id") or "").strip()
        if rule_id:
            rule_ids.append(rule_id[:200])
        if item.get("warning") is True:
            warning_codes.append(code)

    warning_count = _nonnegative_int(payload, "warning_count")
    missing_count = _nonnegative_int(payload, "missing_prerequisite_count")
    approval_missing = payload.get("approval_prerequisite_missing")
    if not isinstance(approval_missing, bool):
        raise ActionGuardError("invalid approval-prerequisite state")
    if approval_missing != ("approval_prerequisite_missing" in warning_codes):
        raise ActionGuardError("inconsistent approval-prerequisite state")
    if warning_count != len(warning_codes):
        raise ActionGuardError("inconsistent policy warning count")

    declared_policy = payload.get("declared_policy")
    policy_id: str | None = None
    policy_version: str | None = None
    if declared_policy is not None:
        if not isinstance(declared_policy, dict):
            raise ActionGuardError("invalid declared-policy metadata")
        policy_id = str(declared_policy.get("policy_id") or "").strip()[:200] or None
        policy_version = str(declared_policy.get("version") or "").strip()[:120] or None

    manifest_sha = str(payload.get("manifest_sha256") or "").strip().lower() or None
    if manifest_sha and not _SHA256_RE.fullmatch(manifest_sha):
        raise ActionGuardError("invalid policy manifest fingerprint")

    return ActionPolicyAdvisory(
        available=True,
        family_key=family_key,
        proposed_step=proposed_step,
        completed_steps=completed_steps,
        declared_policy_status=policy_status,
        advisory_status=advisory_status,
        warning_count=warning_count,
        missing_prerequisite_count=missing_count,
        approval_prerequisite_missing=approval_missing,
        forbidden_step_warning="declared_forbidden_step" in warning_codes,
        nonapproval_prerequisite_missing="prerequisite_missing" in warning_codes,
        policy_id=policy_id,
        policy_version=policy_version,
        manifest_sha256=manifest_sha,
        rule_ids=tuple(rule_ids),
        warning_codes=tuple(warning_codes),
        error_code=None,
    )


class ActionPolicyClient:
    """Least-privilege client for the policy-guard-only advisory capability."""

    def __init__(
        self,
        *,
        fetcher: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        timeout: float = 1.0,
    ) -> None:
        self._fetcher = fetcher
        self._timeout = max(0.05, min(float(timeout), 10.0))

    def advisory(
        self,
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> ActionPolicyAdvisory:
        family = _normalize_family(family_key)
        proposed = _normalize_step(proposed_step, field="proposed_step")
        completed = _normalize_steps(completed_steps)
        request_payload = {
            "family_key": family,
            "proposed_step": proposed,
            "completed_steps": list(completed),
        }
        if self._fetcher is None:
            payload = _default_fetch(request_payload, timeout=self._timeout)
        else:
            try:
                payload = self._fetcher(dict(request_payload))
            except (ActionGuardError, ActionGuardUnavailable):
                raise
            except (TimeoutError, socket.timeout, OSError, ConnectionError) as exc:
                raise ActionGuardUnavailable("policy guard service unavailable") from exc
            if not isinstance(payload, dict):
                raise ActionGuardError("invalid policy guard response")
        return _parse_advisory(
            payload,
            family_key=family,
            proposed_step=proposed,
            completed_steps=completed,
        )

    def try_advisory(self, **kwargs: Any) -> ActionPolicyAdvisory:
        """Represent service unavailability explicitly; invalid integration input still raises."""
        family = _normalize_family(str(kwargs.get("family_key") or ""))
        proposed = _normalize_step(str(kwargs.get("proposed_step") or ""), field="proposed_step")
        completed = _normalize_steps(kwargs.get("completed_steps") or ())
        try:
            return self.advisory(
                family_key=family,
                proposed_step=proposed,
                completed_steps=completed,
            )
        except ActionGuardUnavailable:
            return ActionPolicyAdvisory.unavailable(
                family_key=family,
                proposed_step=proposed,
                completed_steps=completed,
            )


@dataclass(frozen=True)
class ApprovalRequest:
    family_key: str
    proposed_step: str
    policy_id: str | None
    policy_version: str | None
    manifest_sha256: str | None
    rule_ids: tuple[str, ...]
    warning_codes: tuple[str, ...]

    @classmethod
    def from_advisory(cls, advisory: ActionPolicyAdvisory) -> "ApprovalRequest":
        return cls(
            family_key=advisory.family_key,
            proposed_step=advisory.proposed_step,
            policy_id=advisory.policy_id,
            policy_version=advisory.policy_version,
            manifest_sha256=advisory.manifest_sha256,
            rule_ids=advisory.rule_ids,
            warning_codes=advisory.warning_codes,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "family_key": self.family_key,
            "proposed_step": self.proposed_step,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "manifest_sha256": self.manifest_sha256,
            "rule_ids": list(self.rule_ids),
            "warning_codes": list(self.warning_codes),
        }


@dataclass(frozen=True)
class GateDecision:
    mode: str
    execute: bool
    status: str
    reason_codes: tuple[str, ...]
    approval_requested: bool
    human_approved: bool | None
    advisory: ActionPolicyAdvisory
    rechecked_advisory: ActionPolicyAdvisory | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "execute": self.execute,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "approval_requested": self.approval_requested,
            "human_approved": self.human_approved,
            "advisory": self.advisory.as_dict(),
            "rechecked_advisory": self.rechecked_advisory.as_dict() if self.rechecked_advisory else None,
        }


T = TypeVar("T")


@dataclass(frozen=True)
class GuardedActionResult(Generic[T]):
    executed: bool
    decision: GateDecision
    value: T | None = None


class OptInActionGuard:
    """Explicit wrapper for warn-only or human-approval-gated structural actions.

    ``approval_gate`` enforces only a declared *approval predecessor*. Forbidden
    steps and non-approval predecessor warnings remain visible but non-blocking in
    this version; hard-deny policy enforcement is intentionally a separate step.
    """

    def __init__(
        self,
        *,
        mode: str = "warn",
        client: ActionPolicyClient | None = None,
        human_approval: Callable[[ApprovalRequest], bool] | None = None,
        on_notice: Callable[[GateDecision], None] | None = None,
        unavailable_behavior: str = "pause",
        event_sink: BufferedAgentEventSink | None = None,
        event_context: Mapping[str, Any] | None = None,
    ) -> None:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode not in {"warn", "approval_gate"}:
            raise ActionGuardError("mode must be warn or approval_gate")
        unavailable = str(unavailable_behavior or "").strip().lower()
        if unavailable not in {"pause", "continue"}:
            raise ActionGuardError("unavailable_behavior must be pause or continue")
        self._mode = normalized_mode
        self._client = client or ActionPolicyClient()
        self._human_approval = human_approval
        self._on_notice = on_notice
        self._unavailable_behavior = unavailable
        self._event_sink = event_sink
        self._event_context = self._validate_event_context(event_context)
        if (event_sink is None) != (event_context is None):
            raise ActionGuardError("event_sink and event_context must be supplied together")

    @staticmethod
    def _validate_event_context(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ActionGuardError("event_context must be a mapping")
        unknown = set(value) - _EVENT_CONTEXT_KEYS
        if unknown:
            raise ActionGuardError("event_context contains unsupported fields")
        context = {str(key): child for key, child in value.items()}
        if not str(context.get("agent_name") or "").strip():
            raise ActionGuardError("event_context requires agent_name")
        if not str(context.get("run_id") or "").strip() and not str(context.get("trace_id") or "").strip():
            raise ActionGuardError("event_context requires run_id or trace_id")
        sample = {
            **context,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "operation": "human_approval_requested",
            "status": "running",
            "tool_category": "none",
            "sensor_id": "agent:approval-gate",
        }
        try:
            agent_event_to_evidence(sample)
        except (AgentEvidenceError, TypeError, ValueError) as exc:
            raise ActionGuardError("invalid structural event_context") from exc
        return context

    def _emit_approval_event(self, *, operation: str, status: str) -> None:
        if self._event_sink is None or self._event_context is None:
            return
        event = {
            **self._event_context,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "operation": operation,
            "status": status,
            "tool_category": "none",
            "sensor_id": "agent:approval-gate",
        }
        # Telemetry remains best-effort and must not decide whether the gated
        # action runs. The sink validates again and returns False on rejection.
        self._event_sink.emit(event)

    def _notify(self, decision: GateDecision) -> None:
        if self._on_notice is None:
            return
        try:
            self._on_notice(decision)
        except Exception:
            # Notice rendering is advisory UI plumbing, not an execution-policy
            # source. A broken toast/log callback cannot silently change the gate.
            pass

    @staticmethod
    def _warning_reasons(advisory: ActionPolicyAdvisory) -> tuple[str, ...]:
        return advisory.warning_codes or (("policy_warning",) if advisory.warning_count else ())

    def _unavailable_decision(self, advisory: ActionPolicyAdvisory) -> GateDecision:
        if self._mode == "warn" or self._unavailable_behavior == "continue":
            decision = GateDecision(
                mode=self._mode,
                execute=True,
                status="proceed_policy_unavailable",
                reason_codes=("policy_guard_unavailable",),
                approval_requested=False,
                human_approved=None,
                advisory=advisory,
            )
        else:
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="paused_policy_unavailable",
                reason_codes=("policy_guard_unavailable",),
                approval_requested=False,
                human_approved=None,
                advisory=advisory,
            )
        self._notify(decision)
        return decision

    def evaluate(
        self,
        *,
        family_key: str,
        proposed_step: str,
        completed_steps: Iterable[str] = (),
    ) -> GateDecision:
        advisory = self._client.try_advisory(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )
        if not advisory.available:
            return self._unavailable_decision(advisory)

        warnings = self._warning_reasons(advisory)
        if self._mode == "warn":
            decision = GateDecision(
                mode=self._mode,
                execute=True,
                status="proceed_with_warning" if advisory.warning_count else "proceed",
                reason_codes=warnings,
                approval_requested=False,
                human_approved=None,
                advisory=advisory,
            )
            self._notify(decision)
            return decision

        if not advisory.approval_prerequisite_missing:
            decision = GateDecision(
                mode=self._mode,
                execute=True,
                status="proceed_with_warning" if advisory.warning_count else "proceed",
                reason_codes=warnings,
                approval_requested=False,
                human_approved=None,
                advisory=advisory,
            )
            self._notify(decision)
            return decision

        if self._human_approval is None:
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="paused_approval_handler_missing",
                reason_codes=("approval_prerequisite_missing", "human_approval_handler_missing"),
                approval_requested=False,
                human_approved=None,
                advisory=advisory,
            )
            self._notify(decision)
            return decision

        request = ApprovalRequest.from_advisory(advisory)
        self._emit_approval_event(operation="human_approval_requested", status="running")
        try:
            approved = self._human_approval(request)
        except Exception:
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="paused_approval_handler_error",
                reason_codes=("approval_prerequisite_missing", "human_approval_handler_error"),
                approval_requested=True,
                human_approved=None,
                advisory=advisory,
            )
            self._notify(decision)
            return decision
        if not isinstance(approved, bool):
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="paused_invalid_approval_decision",
                reason_codes=("approval_prerequisite_missing", "human_approval_decision_not_boolean"),
                approval_requested=True,
                human_approved=None,
                advisory=advisory,
            )
            self._notify(decision)
            return decision
        if not approved:
            self._emit_approval_event(operation="human_approval_received", status="denied")
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="approval_denied",
                reason_codes=("approval_prerequisite_missing", "human_approval_denied"),
                approval_requested=True,
                human_approved=False,
                advisory=advisory,
            )
            self._notify(decision)
            return decision

        self._emit_approval_event(operation="human_approval_received", status="success")
        recheck_steps = list(advisory.completed_steps)
        for step in ("approval_request", "approval_received:success"):
            if step not in recheck_steps:
                recheck_steps.append(step)
        rechecked = self._client.try_advisory(
            family_key=advisory.family_key,
            proposed_step=advisory.proposed_step,
            completed_steps=recheck_steps,
        )
        if not rechecked.available:
            execute = self._unavailable_behavior == "continue"
            decision = GateDecision(
                mode=self._mode,
                execute=execute,
                status=(
                    "approval_granted_recheck_unavailable"
                    if execute
                    else "paused_policy_recheck_unavailable"
                ),
                reason_codes=("human_approval_granted", "policy_guard_unavailable_after_approval"),
                approval_requested=True,
                human_approved=True,
                advisory=advisory,
                rechecked_advisory=rechecked,
            )
            self._notify(decision)
            return decision
        if rechecked.approval_prerequisite_missing:
            decision = GateDecision(
                mode=self._mode,
                execute=False,
                status="paused_approval_prerequisite_still_missing",
                reason_codes=("human_approval_granted", "approval_prerequisite_still_missing_after_recheck"),
                approval_requested=True,
                human_approved=True,
                advisory=advisory,
                rechecked_advisory=rechecked,
            )
            self._notify(decision)
            return decision

        recheck_warnings = self._warning_reasons(rechecked)
        decision = GateDecision(
            mode=self._mode,
            execute=True,
            status="approval_granted_with_warning" if rechecked.warning_count else "approval_granted",
            reason_codes=("human_approval_granted",) + recheck_warnings,
            approval_requested=True,
            human_approved=True,
            advisory=advisory,
            rechecked_advisory=rechecked,
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
    ) -> GuardedActionResult[T]:
        """Evaluate the gate and invoke a zero-argument action only when permitted.

        The action closure and its return value are never serialized or sent to
        OpenWorkGraph. Exceptions raised by the action itself propagate normally.
        """
        if not callable(action):
            raise ActionGuardError("action must be callable")
        decision = self.evaluate(
            family_key=family_key,
            proposed_step=proposed_step,
            completed_steps=completed_steps,
        )
        if not decision.execute:
            return GuardedActionResult(executed=False, decision=decision, value=None)
        value = action()
        return GuardedActionResult(executed=True, decision=decision, value=value)
