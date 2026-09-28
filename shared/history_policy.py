from __future__ import annotations

"""Local history-retention and saved-history disclosure policy.

Capture, retention, and AI disclosure are separate decisions. This module is in
``shared`` so ingestion can fail closed before persistence without importing the
server layer. The state file contains policy/bookkeeping only, never work text.
"""

import copy
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_POLICY_VERSION = 1
_MAX_SESSION_TOMBSTONES = 5000
_RETENTION_MODES = {"ephemeral", "days", "forever"}
_AI_ACCESS_MODES = {"off", "selected_range", "all_saved"}


def _now_dt() -> datetime:
    return datetime.now(timezone.utc)


def _now() -> str:
    return _now_dt().isoformat()


def _parse(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    path = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def policy_path() -> Path:
    return data_dir() / "history_policy.json"


def _retention(mode: str, days: int | None = None) -> dict[str, Any]:
    selected = str(mode or "").strip().lower()
    if selected not in _RETENTION_MODES:
        raise ValueError("retention mode must be ephemeral, days, or forever")
    if selected == "days":
        amount = int(days or 0)
        if amount < 1 or amount > 3650:
            raise ValueError("retention days must be between 1 and 3650")
        return {"mode": "days", "days": amount}
    return {"mode": selected, "days": None}


RUN_MEMORY_DEFAULT_DAYS = 90


def _run_memory(enabled: Any, days: Any) -> dict[str, Any]:
    """Content-free run memory: one summary per run, kept after raw history is gone."""
    try:
        amount = int(days)
    except Exception:
        amount = RUN_MEMORY_DEFAULT_DAYS
    return {"enabled": bool(enabled), "days": max(1, min(amount, 3650))}


def _default(*, has_existing_evidence: bool) -> dict[str, Any]:
    # Existing installations must never lose history merely by upgrading. New
    # installations start ephemeral until the person makes an informed choice.
    initial = "forever" if has_existing_evidence else "ephemeral"
    return {
        "version": _POLICY_VERSION,
        "onboarding_complete": False,
        "human_retention": _retention(initial),
        "agent_retention": _retention(initial),
        "history_generation": 1,
        "session_tombstones": [],
        "run_memory": _run_memory(True, RUN_MEMORY_DEFAULT_DAYS),
        "ai_history_access": {
            "mode": "off",
            "since": None,
            "until": None,
            "granted_at": None,
            "expires_at": None,
        },
        "created_at": _now(),
        "updated_at": _now(),
        "upgrade_preserved_existing_history": bool(has_existing_evidence),
    }


def _uninitialized_fallback() -> dict[str, Any]:
    """Preserve pre-History retention until the owning runtime initializes it.

    The production runner explicitly initializes History: genuinely new installs
    then receive the ephemeral onboarding default, while upgrades preserve existing
    history. Legacy/embedded callers that import agent ingestion or secure_app
    directly do not run that initialization. Absence of a policy file there must
    not silently turn completed agent runs into disposable data.
    """
    value = _default(has_existing_evidence=True)
    value["upgrade_preserved_existing_history"] = False
    return value


def _normalize(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["version"] = _POLICY_VERSION
    result["onboarding_complete"] = bool(result.get("onboarding_complete"))
    for key in ("human_retention", "agent_retention"):
        raw = result.get(key) if isinstance(result.get(key), dict) else {}
        try:
            result[key] = _retention(str(raw.get("mode") or "ephemeral"), raw.get("days"))
        except Exception:
            result[key] = _retention("ephemeral")
    result["history_generation"] = max(1, int(result.get("history_generation") or 1))
    memory = result.get("run_memory") if isinstance(result.get("run_memory"), dict) else {}
    result["run_memory"] = _run_memory(memory.get("enabled", True), memory.get("days", RUN_MEMORY_DEFAULT_DAYS))
    tombstones = result.get("session_tombstones") if isinstance(result.get("session_tombstones"), list) else []
    safe_tombstones: list[dict[str, str]] = []
    for item in tombstones[-_MAX_SESSION_TOMBSTONES:]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip().lower()
        session_id = str(item.get("session_id") or "").strip()
        if kind in {"human", "agent"} and session_id:
            safe_tombstones.append({
                "kind": kind,
                "session_id": session_id[:240],
                "deleted_at": str(item.get("deleted_at") or _now())[:80],
                "reason": str(item.get("reason") or "retention")[:120],
            })
    result["session_tombstones"] = safe_tombstones
    access = result.get("ai_history_access") if isinstance(result.get("ai_history_access"), dict) else {}
    mode = str(access.get("mode") or "off").strip().lower()
    if mode not in _AI_ACCESS_MODES:
        mode = "off"
    result["ai_history_access"] = {
        "mode": mode,
        "since": str(access.get("since") or "") or None,
        "until": str(access.get("until") or "") or None,
        "granted_at": str(access.get("granted_at") or "") or None,
        "expires_at": str(access.get("expires_at") or "") or None,
    }
    result.setdefault("created_at", _now())
    result["updated_at"] = str(result.get("updated_at") or _now())
    return result


def _write_unlocked(value: dict[str, Any]) -> dict[str, Any]:
    path = policy_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_normalize(value), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="history-policy-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        try:
            os.chmod(path, 0o600)
        except Exception:
            pass
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except Exception:
            pass
    return json.loads(payload)


def initialize_policy(*, has_existing_evidence: bool) -> dict[str, Any]:
    with _LOCK:
        path = policy_path()
        if path.exists():
            return read_policy()
        return _write_unlocked(_default(has_existing_evidence=has_existing_evidence))


def read_policy() -> dict[str, Any]:
    with _LOCK:
        try:
            value = json.loads(policy_path().read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("history policy must be an object")
            return _normalize(value)
        except Exception:
            # History has not been initialized by the owning runtime yet. Keep
            # legacy/direct callers non-destructive; production initialization
            # writes the explicit new-install or upgrade policy before use.
            return _uninitialized_fallback()


def update_retention(
    *,
    human_mode: str,
    human_days: int | None,
    agent_mode: str,
    agent_days: int | None,
    onboarding_complete: bool = True,
) -> dict[str, Any]:
    with _LOCK:
        value = read_policy()
        value["human_retention"] = _retention(human_mode, human_days)
        value["agent_retention"] = _retention(agent_mode, agent_days)
        value["onboarding_complete"] = bool(onboarding_complete)
        value["updated_at"] = _now()
        return _write_unlocked(value)


def run_memory_policy(*, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    return dict((policy or read_policy())["run_memory"])


def update_run_memory(*, enabled: bool, days: int) -> dict[str, Any]:
    amount = int(days)
    if amount < 1 or amount > 3650:
        raise ValueError("run memory days must be between 1 and 3650")
    with _LOCK:
        value = read_policy()
        value["run_memory"] = _run_memory(bool(enabled), amount)
        value["updated_at"] = _now()
        return _write_unlocked(value)


def event_kind(event: dict[str, Any]) -> str:
    if str(event.get("source") or "").strip().lower() == "agent":
        return "agent"
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    if str(metadata.get("actor_kind") or "").strip().lower() == "agent":
        return "agent"
    return "human"


def retention_for_kind(kind: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    selected = "agent" if str(kind).lower() == "agent" else "human"
    value = policy or read_policy()
    return dict(value.get(f"{selected}_retention") or _retention("ephemeral"))


def session_is_tombstoned(kind: str, session_id: str, *, policy: dict[str, Any] | None = None) -> bool:
    sid = str(session_id or "").strip()
    if not sid:
        return False
    selected = "agent" if str(kind).lower() == "agent" else "human"
    value = policy or read_policy()
    return any(
        str(item.get("kind") or "") == selected and str(item.get("session_id") or "") == sid
        for item in value.get("session_tombstones") or []
        if isinstance(item, dict)
    )


def add_session_tombstones(kind: str, session_ids: list[str], *, reason: str) -> dict[str, Any]:
    selected = "agent" if str(kind).lower() == "agent" else "human"
    ids = [str(x).strip()[:240] for x in session_ids if str(x).strip()]
    with _LOCK:
        value = read_policy()
        existing = {
            (str(item.get("kind") or ""), str(item.get("session_id") or "")): item
            for item in value.get("session_tombstones") or []
            if isinstance(item, dict)
        }
        for sid in ids:
            existing[(selected, sid)] = {
                "kind": selected,
                "session_id": sid,
                "deleted_at": _now(),
                "reason": str(reason or "retention")[:120],
            }
        value["session_tombstones"] = list(existing.values())[-_MAX_SESSION_TOMBSTONES:]
        value["history_generation"] = int(value.get("history_generation") or 1) + 1
        value["updated_at"] = _now()
        return _write_unlocked(value)


def bump_history_generation(*, reason: str = "history_changed") -> int:
    with _LOCK:
        value = read_policy()
        value["history_generation"] = int(value.get("history_generation") or 1) + 1
        value["last_history_change_reason"] = str(reason or "history_changed")[:120]
        value["updated_at"] = _now()
        return int(_write_unlocked(value)["history_generation"])


def history_generation() -> int:
    return int(read_policy().get("history_generation") or 1)


def prepare_recordable_event(event: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    value = read_policy()
    kind = event_kind(event)
    session_id = str(event.get("session_id") or "").strip()
    if session_is_tombstoned(kind, session_id, policy=value):
        return None
    retention = retention_for_kind(kind, policy=value)
    if retention.get("mode") == "days":
        observed = _parse(event.get("observed_at"))
        cutoff = (now or _now_dt()) - timedelta(days=int(retention.get("days") or 1))
        if observed is not None and observed < cutoff:
            return None
    return copy.deepcopy(event)


def set_ai_history_access(
    *,
    mode: str,
    since: str | None = None,
    until: str | None = None,
    expires_minutes: int | None = 60,
) -> dict[str, Any]:
    selected = str(mode or "off").strip().lower()
    if selected not in _AI_ACCESS_MODES:
        raise ValueError("AI history access mode must be off, selected_range, or all_saved")
    normalized_since: str | None = None
    normalized_until: str | None = None
    if selected == "selected_range":
        start = _parse(since)
        end = _parse(until)
        if start is None or end is None or end <= start:
            raise ValueError("selected history access requires a valid since/until range")
        normalized_since = start.isoformat()
        normalized_until = end.isoformat()
    minutes = None if expires_minutes is None else int(expires_minutes)
    if minutes is not None and (minutes < 1 or minutes > 24 * 60):
        raise ValueError("AI history access expiry must be between 1 minute and 24 hours")
    granted = _now_dt()
    expires = (granted + timedelta(minutes=minutes)).isoformat() if minutes is not None else None
    with _LOCK:
        value = read_policy()
        previous = dict(value.get("ai_history_access") or {})
        changed = (
            str(previous.get("mode") or "off") != selected
            or (str(previous.get("since") or "") or None) != normalized_since
            or (str(previous.get("until") or "") or None) != normalized_until
        )
        value["ai_history_access"] = {
            "mode": selected,
            "since": normalized_since,
            "until": normalized_until,
            "granted_at": granted.isoformat() if selected != "off" else None,
            "expires_at": expires if selected != "off" else None,
        }
        if changed:
            # Pulse cursors remember delivered finding versions. A disclosure-scope
            # change must rebaseline them so findings hidden under a narrower lease
            # cannot remain incorrectly marked as already delivered later.
            value["history_generation"] = int(value.get("history_generation") or 1) + 1
            value["last_history_change_reason"] = "ai_history_access_changed"
        value["updated_at"] = _now()
        return _write_unlocked(value)["ai_history_access"]


def active_ai_history_access(*, now: datetime | None = None) -> dict[str, Any]:
    value = read_policy()
    access = dict(value.get("ai_history_access") or {})
    selected = str(access.get("mode") or "off")
    expires = _parse(access.get("expires_at"))
    current = now or _now_dt()
    if selected != "off" and expires is not None and current >= expires:
        set_ai_history_access(mode="off", expires_minutes=None)
        return {"mode": "off", "since": None, "until": None, "granted_at": None, "expires_at": None}
    return access


__all__ = [
    "run_memory_policy",
    "update_run_memory",
    "active_ai_history_access",
    "add_session_tombstones",
    "bump_history_generation",
    "event_kind",
    "history_generation",
    "initialize_policy",
    "prepare_recordable_event",
    "read_policy",
    "retention_for_kind",
    "set_ai_history_access",
    "session_is_tombstoned",
    "update_retention",
]
