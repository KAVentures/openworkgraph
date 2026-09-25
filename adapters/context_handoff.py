from __future__ import annotations

"""Explicit, integrity-checked handoff of OpenWorkGraph task context to agents.

This module deliberately does not inject context into prompts, mutate an agent
runtime, choose actions, or enforce policy. A caller must explicitly request a
handoff and decide how its own runtime represents contextual data.

The handoff preserves the exact task-context snapshot fingerprint used by the
existing run-start linkage helpers, so later structural execution evidence can
be associated with the same preflight snapshot without exposing the snapshot in
agent telemetry.
"""

from dataclasses import dataclass
import copy
import hashlib
import json
import re
from typing import Any, Mapping

from .context_link import task_context_link
from .task_preflight import TaskPreflight, TaskPreflightError


_SCHEMA_VERSION = "1.0"
_MAX_HANDOFF_BYTES = 2_000_000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _context_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _copy_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = copy.deepcopy(dict(value))
    except Exception as exc:
        raise TaskPreflightError("task-context snapshot cannot be copied safely") from exc
    if not isinstance(copied, dict):
        raise TaskPreflightError("task-context snapshot must be an object")
    return copied


def _assert_handoff_contract(preflight: TaskPreflight, context: dict[str, Any]) -> None:
    if context.get("read_only") is not True or context.get("writes_performed") is not False:
        raise TaskPreflightError("task-context snapshot no longer preserves read-only semantics")
    if context.get("automatic_context_injection") is not False:
        raise TaskPreflightError("task-context snapshot unexpectedly enables automatic injection")

    authority = context.get("authority_model")
    if not isinstance(authority, dict):
        raise TaskPreflightError("task-context snapshot is missing its authority model")
    if authority.get("automatic_execution") is not False:
        raise TaskPreflightError("task-context snapshot unexpectedly enables automatic execution")
    if authority.get("automatic_policy_enforcement") is not False:
        raise TaskPreflightError("task-context snapshot unexpectedly enables automatic policy enforcement")
    if authority.get("observed_behavior_becomes_policy") is not False:
        raise TaskPreflightError("task-context snapshot unexpectedly promotes observed behavior to policy")
    if authority.get("policy_inferred_from_behavior") is not False:
        raise TaskPreflightError("task-context snapshot unexpectedly infers policy from behavior")

    expected = str(preflight.context_sha256 or "").strip().lower()
    if not _SHA256_RE.fullmatch(expected):
        raise TaskPreflightError("available preflight is missing a valid context fingerprint")
    if _context_sha256(context) != expected:
        raise TaskPreflightError("task-context snapshot fingerprint mismatch")

    resolution = context.get("resolution") if isinstance(context.get("resolution"), dict) else {}
    available = context.get("context_available") is True
    if available != bool(preflight.context_resolved):
        raise TaskPreflightError("task-context handoff resolution no longer matches preflight")

    if preflight.context_resolved:
        if resolution.get("status") != "resolved":
            raise TaskPreflightError("resolved preflight has a non-resolved task-context snapshot")
        family = str(resolution.get("family_key") or "").strip().lower()
        if not family or family != str(preflight.family_key or "").strip().lower():
            raise TaskPreflightError("task-context family no longer matches preflight")
        task_context = context.get("task_context")
        if not isinstance(task_context, dict):
            raise TaskPreflightError("resolved task-context snapshot is missing task_context")
    elif context.get("task_context") not in (None, {}):
        raise TaskPreflightError("unresolved preflight unexpectedly carries resolved task context")


def _consumer_contract() -> dict[str, Any]:
    return {
        "explicit_opt_in_required": True,
        "automatic_context_injection": False,
        "automatic_execution": False,
        "automatic_policy_enforcement": False,
        "observed_procedure_authoritative": False,
        "observed_behavior_is_permission_source": False,
        "observed_context_may_contain_untrusted_data": True,
        "embedded_observed_data_is_agent_instruction": False,
        "hidden_model_reasoning_requested_or_exposed": False,
    }


@dataclass(frozen=True)
class AgentContextHandoff:
    """One bounded contextual-data envelope plus matching run-start linkage."""

    available: bool
    context_resolved: bool
    family_key: str | None
    context_sha256: str | None
    policy_manifest_sha256: str | None
    envelope: Mapping[str, Any]
    run_start_linkage: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.envelope))

    def linkage_dict(self) -> dict[str, Any]:
        return copy.deepcopy(dict(self.run_start_linkage))

    def to_json(self, *, indent: int | None = None) -> str:
        value = json.dumps(
            self.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            separators=None if indent is not None else (",", ":"),
        )
        if len(value.encode("utf-8")) > _MAX_HANDOFF_BYTES:
            raise TaskPreflightError("task-context handoff exceeds safety limit")
        return value


def build_context_handoff(preflight: TaskPreflight) -> AgentContextHandoff:
    """Build an explicit contextual-data envelope from one verified preflight.

    The returned ``run_start_linkage`` is the same privacy-minimized assertion
    accepted by ``attach_preflight_to_run_started``. The full context stays in
    the caller-controlled handoff and is never copied into structural telemetry.
    """
    if not isinstance(preflight, TaskPreflight):
        raise TaskPreflightError("preflight must be a TaskPreflight result")

    linkage = task_context_link(preflight)
    contract = _consumer_contract()

    if not preflight.available:
        if preflight.context is not None:
            raise TaskPreflightError("unavailable preflight unexpectedly contains task context")
        envelope = {
            "schema_version": _SCHEMA_VERSION,
            "kind": "openworkgraph_task_context_handoff",
            "available": False,
            "context_resolved": False,
            "family_key": None,
            "context_sha256": None,
            "policy_manifest_sha256": None,
            "consumer_contract": contract,
            "run_start_linkage": dict(linkage),
            "context": None,
        }
        return AgentContextHandoff(
            available=False,
            context_resolved=False,
            family_key=None,
            context_sha256=None,
            policy_manifest_sha256=None,
            envelope=envelope,
            run_start_linkage=dict(linkage),
        )

    if not isinstance(preflight.context, Mapping):
        raise TaskPreflightError("available preflight is missing its task-context snapshot")
    context = _copy_mapping(preflight.context)
    _assert_handoff_contract(preflight, context)

    envelope = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "openworkgraph_task_context_handoff",
        "available": True,
        "context_resolved": bool(preflight.context_resolved),
        "family_key": preflight.family_key,
        "context_sha256": preflight.context_sha256,
        "policy_manifest_sha256": preflight.policy_manifest_sha256,
        "consumer_contract": contract,
        "run_start_linkage": dict(linkage),
        "context": context,
    }
    compact = json.dumps(
        envelope,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(compact) > _MAX_HANDOFF_BYTES:
        raise TaskPreflightError("task-context handoff exceeds safety limit")

    return AgentContextHandoff(
        available=True,
        context_resolved=bool(preflight.context_resolved),
        family_key=preflight.family_key,
        context_sha256=preflight.context_sha256,
        policy_manifest_sha256=preflight.policy_manifest_sha256,
        envelope=envelope,
        run_start_linkage=dict(linkage),
    )
