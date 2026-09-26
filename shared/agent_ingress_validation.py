from __future__ import annotations

"""Strict validation for untrusted agent-ingest structural fields.

Agent adapters intentionally submit structural identifiers rather than arbitrary
human-readable content. Rejecting non-ID-shaped values at the shared ingest
boundary prevents commands, paths, names, prompts, and credential-shaped values
from being smuggled into fields that downstream code treats as safe metadata.
"""

from datetime import datetime, timedelta, timezone
import re
from typing import Any

from .agent_evidence import AgentEvidenceError
from .time_utils import normalize_timestamp


MAX_AGENT_FUTURE_SKEW_SECONDS = 10 * 60

_STRUCTURAL_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,128}$")
# Defense in depth for credential strings that are syntactically identifier-like.
_SECRET_LIKE_RE = re.compile(
    r"^(?:"
    r"AKIA[0-9A-Z]{16}"
    r"|ASIA[0-9A-Z]{16}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{30,}"
    r"|github_pat_[A-Za-z0-9_]{30,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|(?:sk|rk|pk)_live_[A-Za-z0-9]{16,}"
    r"|AIza[0-9A-Za-z_-]{35}"
    r")$",
    re.IGNORECASE,
)

_STRUCTURAL_FIELDS = (
    "event_id",
    "organization_id",
    "actor_id",
    "device_id",
    "sensor_id",
    "session_id",
    "run_id",
    "trace_id",
    "span_id",
    "parent_span_id",
    "workflow_id",
    "trigger_event_id",
    "tool_name",
)


def _structural_identifier(value: Any, *, field: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if not _STRUCTURAL_ID_RE.fullmatch(raw):
        raise AgentEvidenceError(f"invalid structural identifier: {field}")
    if _SECRET_LIKE_RE.fullmatch(raw):
        raise AgentEvidenceError(f"credential-shaped value is not allowed in {field}")
    return raw


def _validated_observed_at(value: Any, *, now: datetime | None = None) -> str:
    try:
        normalized = normalize_timestamp(str(value or ""))
    except ValueError as exc:
        raise AgentEvidenceError("observed_at must be timezone-aware ISO-8601") from exc
    parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    current = now or datetime.now(timezone.utc)
    if parsed > current + timedelta(seconds=MAX_AGENT_FUTURE_SKEW_SECONDS):
        raise AgentEvidenceError("observed_at is too far in the future")
    return normalized


def validate_agent_ingress_event(
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a validated copy of one untrusted agent event.

    Historical/backfilled events remain valid. Future timestamps get a small
    clock-skew allowance, while all accepted timestamps are normalized to UTC.
    Empty optional identifiers remain empty so existing adapters can omit them.
    """
    if not isinstance(payload, dict):
        raise AgentEvidenceError("agent event must be an object")
    out = dict(payload)
    out["observed_at"] = _validated_observed_at(out.get("observed_at"), now=now)
    for field in _STRUCTURAL_FIELDS:
        if field not in out:
            continue
        out[field] = _structural_identifier(out.get(field), field=field)
    return out
