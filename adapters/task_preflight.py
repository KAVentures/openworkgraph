from __future__ import annotations

"""Reusable, non-prescriptive client for OpenWorkGraph task-context preflight.

This module is intentionally a *reader*, not an execution policy engine. It
retrieves the read-only /v1/task-context contract and exposes a compact summary
that agent runtimes can inspect before acting. It never formats hidden prompt
instructions, chooses an action, or converts observed behavior into policy.

Unavailable local context can be represented explicitly through try_preflight()
without making OpenWorkGraph a hard runtime dependency. Invalid caller input is
not treated as observer unavailability and therefore remains an error.
"""

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
import socket
from typing import Any, Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from server.local_auth import ensure_api_token


DEFAULT_API = "http://127.0.0.1:8787"
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_STRUCTURAL_STEPS = 48


class TaskPreflightError(ValueError):
    """Invalid preflight request or malformed task-context response."""


class TaskPreflightUnavailable(RuntimeError):
    """The configured OpenWorkGraph observer could not provide task context."""


@dataclass(frozen=True)
class TaskPreflight:
    """Stable, intentionally non-prescriptive view of one task-context response."""

    available: bool
    context_resolved: bool
    family_key: str | None
    resolution_status: str
    resolution_mode: str
    policy_status: str
    policy_present: bool
    policy_authoritative_as_declared_input: bool
    observed_procedure_authoritative: bool
    potential_divergence_count: int
    observed_failure_pattern_count: int
    approval_pattern_count: int
    similar_run_count: int
    next_observed_step_count: int
    automatic_enforcement: bool
    automatic_execution: bool
    context_sha256: str | None
    policy_manifest_sha256: str | None
    error_code: str | None
    context: Mapping[str, Any] | None

    @classmethod
    def unavailable(cls) -> "TaskPreflight":
        return cls(
            available=False,
            context_resolved=False,
            family_key=None,
            resolution_status="unavailable",
            resolution_mode="none",
            policy_status="unknown",
            policy_present=False,
            policy_authoritative_as_declared_input=False,
            observed_procedure_authoritative=False,
            potential_divergence_count=0,
            observed_failure_pattern_count=0,
            approval_pattern_count=0,
            similar_run_count=0,
            next_observed_step_count=0,
            automatic_enforcement=False,
            automatic_execution=False,
            context_sha256=None,
            policy_manifest_sha256=None,
            error_code="observer_unavailable",
            context=None,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly copy without adding instructions or recommendations."""
        return {
            "available": self.available,
            "context_resolved": self.context_resolved,
            "family_key": self.family_key,
            "resolution_status": self.resolution_status,
            "resolution_mode": self.resolution_mode,
            "policy_status": self.policy_status,
            "policy_present": self.policy_present,
            "policy_authoritative_as_declared_input": self.policy_authoritative_as_declared_input,
            "observed_procedure_authoritative": self.observed_procedure_authoritative,
            "potential_divergence_count": self.potential_divergence_count,
            "observed_failure_pattern_count": self.observed_failure_pattern_count,
            "approval_pattern_count": self.approval_pattern_count,
            "similar_run_count": self.similar_run_count,
            "next_observed_step_count": self.next_observed_step_count,
            "automatic_enforcement": self.automatic_enforcement,
            "automatic_execution": self.automatic_execution,
            "context_sha256": self.context_sha256,
            "policy_manifest_sha256": self.policy_manifest_sha256,
            "error_code": self.error_code,
            "context": dict(self.context) if self.context is not None else None,
        }


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
        raise TaskPreflightError("invalid WORKFLOW_OBSERVER_API")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise TaskPreflightError("WORKFLOW_OBSERVER_API must not contain credentials, query, or fragment")
    local = _is_loopback(parsed.hostname)
    if not local and os.getenv("OWG_PREFLIGHT_ALLOW_REMOTE", "").strip() != "1":
        raise TaskPreflightError("remote task-context preflight requires OWG_PREFLIGHT_ALLOW_REMOTE=1")
    return raw, local


def _api_token(*, local: bool) -> str:
    configured = os.getenv("OWG_API_TOKEN", "").strip()
    if configured:
        return configured
    if local:
        return ensure_api_token()
    raise TaskPreflightError("remote task-context preflight requires OWG_API_TOKEN")


def _bounded_int(value: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise TaskPreflightError("invalid numeric preflight option") from exc
    return max(minimum, min(parsed, maximum))


def _normalize_steps(current_steps: Iterable[str]) -> list[str]:
    values = [str(item).strip().lower() for item in current_steps if str(item).strip()]
    if len(values) > _MAX_STRUCTURAL_STEPS:
        raise TaskPreflightError("too many structural steps")
    if any("," in item for item in values):
        raise TaskPreflightError("structural step tokens must not contain commas")
    return values


def _query_params(
    *,
    family_key: str,
    task_family: str,
    current_steps: Iterable[str],
    after_step: str,
    min_support: int,
    run_limit: int,
    section_limit: int,
    max_steps_per_run: int,
    max_evidence_refs_per_item: int,
) -> dict[str, str | int]:
    steps = _normalize_steps(current_steps)
    params: dict[str, str | int] = {
        "min_support": _bounded_int(min_support, minimum=2, maximum=100),
        "run_limit": _bounded_int(run_limit, minimum=1, maximum=5),
        "section_limit": _bounded_int(section_limit, minimum=1, maximum=5),
        "max_steps_per_run": _bounded_int(max_steps_per_run, minimum=1, maximum=24),
        "max_evidence_refs_per_item": _bounded_int(max_evidence_refs_per_item, minimum=0, maximum=4),
    }
    if family_key:
        params["family_key"] = str(family_key).strip().lower()
    if task_family:
        params["task_family"] = str(task_family).strip().lower()
    if steps:
        params["current_steps"] = ",".join(steps)
    if after_step:
        after = str(after_step).strip().lower()
        if "," in after:
            raise TaskPreflightError("after_step must be one structural token")
        params["after_step"] = after
    return params


def _default_fetch(params: dict[str, str | int], *, timeout: float) -> dict[str, Any]:
    base, local = _base_url()
    token = _api_token(local=local)
    request = Request(
        f"{base}/v1/task-context?{urlencode(params)}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=max(0.05, min(float(timeout), 10.0))) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        if 400 <= int(exc.code) < 500:
            raise TaskPreflightError("task-context request rejected") from exc
        raise TaskPreflightUnavailable("task-context service unavailable") from exc
    except (URLError, TimeoutError, socket.timeout, OSError) as exc:
        raise TaskPreflightUnavailable("task-context service unavailable") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise TaskPreflightError("task-context response exceeds client safety limit")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TaskPreflightError("invalid task-context response") from exc
    if not isinstance(value, dict):
        raise TaskPreflightError("invalid task-context response")
    return value


def _count_comparison_divergences(value: Any) -> int:
    if not isinstance(value, dict):
        return 0
    executions = value.get("executions")
    if isinstance(executions, list):
        return sum(
            1
            for item in executions
            if isinstance(item, dict) and str(item.get("status") or "") == "potential_divergence"
        )
    results = value.get("results")
    if isinstance(results, list):
        return sum(
            1
            for item in results
            if isinstance(item, dict) and str(item.get("status") or "") == "potential_divergence"
        )
    return int(value.get("potential_divergence_count") or 0)


def _context_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parse_preflight(payload: dict[str, Any]) -> TaskPreflight:
    if payload.get("read_only") is not True or payload.get("writes_performed") is not False:
        raise TaskPreflightError("task-context response does not preserve read-only contract")
    authority = payload.get("authority_model")
    if not isinstance(authority, dict):
        raise TaskPreflightError("task-context response missing authority model")
    if authority.get("automatic_execution") is not False or authority.get("automatic_policy_enforcement") is not False:
        raise TaskPreflightError("task-context response unexpectedly enables execution or enforcement")

    resolution = payload.get("resolution") if isinstance(payload.get("resolution"), dict) else {}
    context_available = payload.get("context_available") is True
    task_context = payload.get("task_context") if isinstance(payload.get("task_context"), dict) else None
    if context_available != bool(task_context):
        raise TaskPreflightError("inconsistent task-context availability")

    policy: dict[str, Any] = {}
    observed: dict[str, Any] = {}
    comparison: Any = None
    if task_context is not None:
        policy = task_context.get("policy") if isinstance(task_context.get("policy"), dict) else {}
        observed = task_context.get("observed_procedure") if isinstance(task_context.get("observed_procedure"), dict) else {}
        comparison = task_context.get("policy_observation_comparison")
        if observed.get("authoritative") is not False or observed.get("prescriptive") is not False:
            raise TaskPreflightError("observed procedure authority contract changed")

    policy_status = str(policy.get("status") or ("not_declared" if context_available else "unknown"))
    family_key = str(resolution.get("family_key") or "").strip() or None
    failures = observed.get("failure_patterns") if isinstance(observed.get("failure_patterns"), list) else []
    approvals = observed.get("approval_patterns") if isinstance(observed.get("approval_patterns"), list) else []
    runs = observed.get("similar_runs") if isinstance(observed.get("similar_runs"), list) else []
    next_steps = observed.get("next_observed_steps") if isinstance(observed.get("next_observed_steps"), list) else []
    manifest_sha = str(policy.get("manifest_sha256") or "").strip() or None

    return TaskPreflight(
        available=True,
        context_resolved=context_available,
        family_key=family_key,
        resolution_status=str(resolution.get("status") or "unknown"),
        resolution_mode=str(resolution.get("mode") or "unknown"),
        policy_status=policy_status,
        policy_present=policy_status == "active",
        policy_authoritative_as_declared_input=bool(policy.get("authoritative_as_declared_input")),
        observed_procedure_authoritative=False,
        potential_divergence_count=_count_comparison_divergences(comparison),
        observed_failure_pattern_count=len(failures),
        approval_pattern_count=len(approvals),
        similar_run_count=len(runs),
        next_observed_step_count=len(next_steps),
        automatic_enforcement=False,
        automatic_execution=False,
        context_sha256=_context_sha256(payload),
        policy_manifest_sha256=manifest_sha,
        error_code=None,
        context=payload,
    )


class TaskPreflightClient:
    """Small transport-independent client for pre-action task-context retrieval.

    ``fetcher`` receives the validated query-parameter dictionary and should
    return the JSON-decoded /v1/task-context payload. This makes the same client
    reusable in custom runtimes that already own their authenticated transport.
    """

    def __init__(
        self,
        *,
        fetcher: Callable[[dict[str, str | int]], dict[str, Any]] | None = None,
        timeout: float = 1.5,
    ) -> None:
        self._timeout = max(0.05, min(float(timeout), 10.0))
        self._fetcher = fetcher

    def preflight(
        self,
        *,
        family_key: str = "",
        task_family: str = "",
        current_steps: Iterable[str] = (),
        after_step: str = "",
        min_support: int = 2,
        run_limit: int = 3,
        section_limit: int = 3,
        max_steps_per_run: int = 16,
        max_evidence_refs_per_item: int = 2,
    ) -> TaskPreflight:
        params = _query_params(
            family_key=family_key,
            task_family=task_family,
            current_steps=current_steps,
            after_step=after_step,
            min_support=min_support,
            run_limit=run_limit,
            section_limit=section_limit,
            max_steps_per_run=max_steps_per_run,
            max_evidence_refs_per_item=max_evidence_refs_per_item,
        )
        if self._fetcher is None:
            payload = _default_fetch(params, timeout=self._timeout)
        else:
            try:
                payload = self._fetcher(dict(params))
            except TaskPreflightError:
                raise
            except TaskPreflightUnavailable:
                raise
            except (TimeoutError, socket.timeout, OSError, ConnectionError) as exc:
                raise TaskPreflightUnavailable("task-context service unavailable") from exc
            if not isinstance(payload, dict):
                raise TaskPreflightError("invalid task-context response")
        return _parse_preflight(payload)

    def try_preflight(self, **kwargs: Any) -> TaskPreflight:
        """Fail open only for observer availability; invalid integration input still raises."""
        try:
            return self.preflight(**kwargs)
        except TaskPreflightUnavailable:
            return TaskPreflight.unavailable()
