from __future__ import annotations

"""Purpose-limited capture scope for temporary workflow discovery studies.

Discovery Mode is intentionally additive. When no discovery session is enabled,
this module is a no-op and normal OpenWorkGraph capture semantics are unchanged.
When enabled, the positive allowlist is enforced before persistence.
"""

import copy
import fnmatch
import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.RLock()
_VERSION = 1
_MAX_DAYS = 31
_MAX_PATTERNS = 64

_BROWSER_APP_TOKENS = (
    "chrome", "chromium", "safari", "firefox", "edge", "brave", "arc",
    "opera", "vivaldi", "zen browser", "mullvad browser", "tor browser",
)


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


def _data_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    path = Path(os.getenv("WORKFLOW_OBSERVER_DATA", root / "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> Path:
    return _data_dir() / "discovery_session.json"


def _clean_patterns(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or len(value) > 200:
            continue
        if value not in result:
            result.append(value)
        if len(result) >= _MAX_PATTERNS:
            break
    return result


def _default() -> dict[str, Any]:
    return {
        "version": _VERSION,
        "enabled": False,
        "status": "inactive",
        "session_id": None,
        "name": "",
        "purpose": "",
        "starts_at": None,
        "ends_at": None,
        "finished_at": None,
        "deactivated_at": None,
        "allowed_apps": [],
        "allowed_browser_hosts": [],
        "allow_unresolved_browser_container": False,
        "employee_review_required": True,
        "share_approved_at": None,
        "excluded_execution_ids": [],
        "questions": [],
    }


def _normalize(value: dict[str, Any] | None) -> dict[str, Any]:
    base = _default()
    if isinstance(value, dict):
        base.update(value)
    base["version"] = _VERSION
    base["enabled"] = bool(base.get("enabled"))
    status = str(base.get("status") or "inactive")
    if status not in {"inactive", "active", "review"}:
        status = "inactive"
    base["status"] = status
    base["allowed_apps"] = _clean_patterns(base.get("allowed_apps"))
    base["allowed_browser_hosts"] = [x.lower().rstrip(".") for x in _clean_patterns(base.get("allowed_browser_hosts"))]
    base["allow_unresolved_browser_container"] = bool(base.get("allow_unresolved_browser_container", False))
    base["employee_review_required"] = True
    base["excluded_execution_ids"] = [
        str(x) for x in (base.get("excluded_execution_ids") or [])
        if str(x or "").strip()
    ][:500]
    questions = []
    for item in base.get("questions") or []:
        if not isinstance(item, dict):
            continue
        q = {
            "question_id": str(item.get("question_id") or ""),
            "question": str(item.get("question") or "")[:2000],
            "answer": str(item.get("answer") or "")[:10000],
            "source": str(item.get("source") or "derived_prompt"),
            "related_execution_ids": [str(x) for x in (item.get("related_execution_ids") or []) if str(x or "").strip()][:50],
            "created_at": item.get("created_at"),
            "answered_at": item.get("answered_at"),
        }
        if q["question_id"] and q["question"]:
            questions.append(q)
    base["questions"] = questions[:200]
    return base


def read_state() -> dict[str, Any]:
    with _LOCK:
        try:
            raw = json.loads(state_path().read_text(encoding="utf-8"))
        except Exception:
            return _default()
        return _normalize(raw if isinstance(raw, dict) else None)


def _write(value: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize(value)
    path = state_path()
    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix="discovery-", suffix=".tmp", dir=str(path.parent))
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
    return normalized


def start_session(
    *,
    name: str,
    purpose: str,
    allowed_apps: list[str] | None = None,
    allowed_browser_hosts: list[str] | None = None,
    duration_days: float = 5,
    ends_at: str | None = None,
    allow_unresolved_browser_container: bool = False,
) -> dict[str, Any]:
    apps = _clean_patterns(allowed_apps)
    hosts = [x.lower().rstrip(".") for x in _clean_patterns(allowed_browser_hosts)]
    if not apps and not hosts:
        raise ValueError("Discovery Mode requires at least one allowed app or browser host")
    now = _now_dt()
    if ends_at:
        end = _parse(ends_at)
        if end is None:
            raise ValueError("ends_at must be an ISO-8601 timestamp with timezone")
    else:
        try:
            days = float(duration_days)
        except Exception as exc:
            raise ValueError("duration_days must be numeric") from exc
        if days <= 0 or days > _MAX_DAYS:
            raise ValueError(f"duration_days must be greater than 0 and at most {_MAX_DAYS}")
        end = now + timedelta(days=days)
    if end <= now or end > now + timedelta(days=_MAX_DAYS):
        raise ValueError(f"Discovery Mode must end within {_MAX_DAYS} days")
    value = {
        **_default(),
        "enabled": True,
        "status": "active",
        "session_id": "disc_" + uuid.uuid4().hex[:20],
        "name": str(name or "Workflow discovery").strip()[:200],
        "purpose": str(purpose or "").strip()[:2000],
        "starts_at": now.isoformat(),
        "ends_at": end.isoformat(),
        "allowed_apps": apps,
        "allowed_browser_hosts": hosts,
        "allow_unresolved_browser_container": bool(allow_unresolved_browser_container),
    }
    with _LOCK:
        return _write(value)


def finish_session() -> dict[str, Any]:
    with _LOCK:
        value = read_state()
        if not value.get("enabled"):
            return value
        if value.get("status") == "active":
            value["status"] = "review"
            value["finished_at"] = _now()
            # Narrow the evidence window to the explicit finish time when the
            # employee ends a study before its scheduled deadline.
            finish = _parse(value["finished_at"])
            scheduled = _parse(value.get("ends_at"))
            if finish and (scheduled is None or finish < scheduled):
                value["ends_at"] = finish.isoformat()
        return _write(value)


def deactivate_session() -> dict[str, Any]:
    """Exit Discovery Mode and restore ordinary OWG capture behavior."""
    with _LOCK:
        value = read_state()
        value["enabled"] = False
        value["status"] = "inactive"
        value["deactivated_at"] = _now()
        return _write(value)


def set_excluded_execution_ids(values: list[str]) -> dict[str, Any]:
    with _LOCK:
        state = read_state()
        state["excluded_execution_ids"] = [str(x) for x in values if str(x or "").strip()][:500]
        return _write(state)


def save_question(
    *,
    question: str,
    answer: str = "",
    question_id: str | None = None,
    source: str = "derived_prompt",
    related_execution_ids: list[str] | None = None,
) -> dict[str, Any]:
    text = str(question or "").strip()
    if not text:
        raise ValueError("question is required")
    with _LOCK:
        state = read_state()
        qid = str(question_id or "").strip() or ("dq_" + uuid.uuid4().hex[:16])
        existing = next((x for x in state["questions"] if x.get("question_id") == qid), None)
        now = _now()
        if existing is None:
            existing = {
                "question_id": qid,
                "question": text[:2000],
                "answer": "",
                "source": str(source or "derived_prompt")[:80],
                "related_execution_ids": [str(x) for x in (related_execution_ids or []) if str(x or "").strip()][:50],
                "created_at": now,
                "answered_at": None,
            }
            state["questions"].append(existing)
        else:
            existing["question"] = text[:2000]
            if related_execution_ids is not None:
                existing["related_execution_ids"] = [str(x) for x in related_execution_ids if str(x or "").strip()][:50]
        if str(answer or "").strip():
            existing["answer"] = str(answer).strip()[:10000]
            existing["answered_at"] = now
        return _write(state)


def answer_question(question_id: str, answer: str) -> dict[str, Any]:
    qid = str(question_id or "").strip()
    if not qid:
        raise ValueError("question_id is required")
    with _LOCK:
        state = read_state()
        existing = next((x for x in state["questions"] if x.get("question_id") == qid), None)
        if existing is None:
            raise ValueError("question not found")
        existing["answer"] = str(answer or "").strip()[:10000]
        existing["answered_at"] = _now() if existing["answer"] else None
        return _write(state)


def approve_share() -> dict[str, Any]:
    with _LOCK:
        state = read_state()
        if not state.get("enabled"):
            raise ValueError("no Discovery Mode session is available for review")
        state["share_approved_at"] = _now()
        return _write(state)


def _browser_like(app: str) -> bool:
    value = str(app or "").casefold()
    return any(token in value for token in _BROWSER_APP_TOKENS)


def _metadata(event: dict[str, Any]) -> dict[str, Any]:
    value = event.get("metadata")
    return value if isinstance(value, dict) else {}


def _host(event: dict[str, Any]) -> str:
    meta = _metadata(event)
    page = meta.get("page") if isinstance(meta.get("page"), dict) else {}
    for candidate in (
        event.get("hostname"),
        event.get("page_host"),
        meta.get("hostname"),
        meta.get("page_host"),
        page.get("hostname"),
        page.get("host"),
    ):
        value = str(candidate or "").strip().lower().rstrip(".")
        if value:
            return value
    return ""


def _matches(value: str, patterns: list[str]) -> bool:
    folded = value.casefold()
    for raw in patterns:
        pattern = str(raw or "").casefold()
        if fnmatch.fnmatchcase(folded, pattern):
            return True
    return False


def _host_matches(host: str, patterns: list[str]) -> bool:
    candidate = host.lower().rstrip(".")
    for raw in patterns:
        pattern = str(raw or "").lower().rstrip(".")
        if fnmatch.fnmatchcase(candidate, pattern):
            return True
        # A bare domain includes subdomains; "*.example.com" remains supported.
        if "*" not in pattern and (candidate == pattern or candidate.endswith("." + pattern)):
            return True
    return False


def event_allowed(event: dict[str, Any], *, state: dict[str, Any] | None = None) -> tuple[bool, str]:
    """Return whether one event may be persisted under the active discovery scope."""
    value = _normalize(state) if state is not None else read_state()
    if not value.get("enabled"):
        return True, "discovery_inactive"

    observed = _parse(event.get("observed_at")) or _now_dt()
    start = _parse(value.get("starts_at"))
    end = _parse(value.get("ends_at"))
    if start is not None and observed < start:
        return False, "before_discovery_window"
    if end is not None and observed >= end:
        return False, "after_discovery_window"

    app = str(event.get("app") or "").strip()
    host = _host(event)
    apps = list(value.get("allowed_apps") or [])
    hosts = list(value.get("allowed_browser_hosts") or [])

    if host:
        if hosts and _host_matches(host, hosts):
            return True, "allowed_browser_host"
        # Do not let a generic browser app allowlist bypass a narrower host scope.
        if app and not _browser_like(app) and apps and _matches(app, apps):
            return True, "allowed_native_app"
        return False, "browser_host_out_of_scope"

    if app and _browser_like(app):
        if value.get("allow_unresolved_browser_container") and apps and _matches(app, apps):
            return True, "allowed_unresolved_browser_container"
        return False, "unresolved_browser_context"

    if app and apps and _matches(app, apps):
        return True, "allowed_native_app"
    return False, "app_out_of_scope"


def prepare_recordable_event(event: dict[str, Any], *, state: dict[str, Any] | None = None) -> dict[str, Any] | None:
    allowed, _reason = event_allowed(event, state=state)
    return dict(event) if allowed else None


def public_state() -> dict[str, Any]:
    value = read_state()
    now = _now_dt()
    end = _parse(value.get("ends_at"))
    status = str(value.get("status") or "inactive")
    if value.get("enabled") and end is not None and now >= end and status == "active":
        status = "review"
    return {
        **value,
        "status": status,
        "expired": bool(value.get("enabled") and end is not None and now >= end),
        "capture_scope_is_positive_allowlist": bool(value.get("enabled")),
        "capture_scope_enforced_before_persistence": True,
        "normal_owg_behavior_when_inactive": True,
        "automatic_sharing": False,
    }


__all__ = [
    "answer_question",
    "approve_share",
    "deactivate_session",
    "event_allowed",
    "finish_session",
    "prepare_recordable_event",
    "public_state",
    "read_state",
    "save_question",
    "set_excluded_execution_ids",
    "start_session",
    "state_path",
]
