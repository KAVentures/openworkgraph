from __future__ import annotations

"""Explicit, local-only review intentions for verified-at-save Open Work cases.

A stored review intention is the *user's request to assess a candidate*, not
proof that a provider record remains open, that a contractual duty exists,
or that AI may act. Never persist provider native IDs, credentials, customer
data, invoice amounts, policy text, or raw observations.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
from typing import Any

from .local_auth import auth_dir
from shared.history_policy import read_policy

_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_INTENTS = frozenset({"assess_follow_up", "verify_payment_status", "check_approved_policy"})
_FILENAME = ".open_work_review_intentions.json"
_TTL_DAYS = 7
_MAX_ITEMS = 50
_MAX_FILE_BYTES = 32_768
_LOCK = threading.RLock()


def _path() -> Path:
    return auth_dir() / _FILENAME


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _allowed_days() -> int | None:
    """Keep review records no longer than the owner's chosen history window."""
    try:
        retention = read_policy().get("human_retention", {})
        if not isinstance(retention, dict):
            return None
        mode = retention.get("mode")
        if mode == "ephemeral":
            return None
        if mode == "forever":
            return _TTL_DAYS
        if mode == "days":
            days = retention.get("days")
            return min(_TTL_DAYS, days) if type(days) is int and days > 0 else None
    except (OSError, ValueError, TypeError):
        return None
    return None


def _read(now: datetime) -> dict[str, dict[str, Any]]:
    max_days = _allowed_days()
    if max_days is None:
        # A session-only history choice must not retain a previous durable
        # manual-review file after this subsystem is next accessed.
        try:
            _path().unlink(missing_ok=True)
        except OSError:
            pass
        return {}
    path = _path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > _MAX_FILE_BYTES:
            return {}
        if os.name == "posix" and (info.st_mode & 0o077):
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("items"), list):
        return {}
    result = {}
    for item in raw["items"][:_MAX_ITEMS]:
        if not isinstance(item, dict):
            continue
        ref, intent = item.get("work_item_ref"), item.get("review_intent")
        created, expires = _time(item.get("created_at")), _time(item.get("expires_at"))
        if (not isinstance(ref, str) or not _REF.fullmatch(ref) or
            intent not in _INTENTS or not created or not expires or
            created > now + timedelta(minutes=5) or expires <= now or
            expires - created > timedelta(days=_TTL_DAYS, minutes=1) or
            created + timedelta(days=max_days) <= now):
            continue
        result[ref] = {
            "work_item_ref": ref,
            "review_intent": intent,
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
            "review_status": "human_review_requested",
            "current_source_state": "unknown_until_explicit_recheck",
            "business_obligation_verified": False,
            "agent_execution_authorized": False,
        }
    return result


def _write(items: dict[str, dict[str, Any]]) -> None:
    path = _path()
    if path.is_symlink():
        raise OSError("unsafe Open Work review file path")
    payload = {"version": 1, "items": list(items.values())}
    serialized = json.dumps(payload, separators=(",", ":")) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_FILE_BYTES:
        raise ValueError("Open Work review queue exceeds local storage limit")
    fd, temp = tempfile.mkstemp(prefix=".owg-review-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(serialized)
            out.flush()
            os.fsync(out.fileno())
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def list_review_intentions(*, now: datetime | None = None) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    max_days = _allowed_days()
    if max_days is None:
        raise ValueError("saved reviews are unavailable with session-only history retention")
    with _LOCK:
        items = _read(current)
    return {
        "format": "openworkgraph.local-review-intentions.v1",
        "items": sorted(items.values(), key=lambda v: v["created_at"], reverse=True),
        "current_source_state": "not_refreshed",
        "source_read_performed": False,
        "owner_declared_intention_not_business_obligation": True,
        "no_agent_execution_rights": True,
        "not_synced": True,
    }


def save_review_intention(
    work_item_ref: str, review_intent: str, *,
    provider_candidate_currently_open: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Only call after separate explicit provider inventory confirmation."""
    if not _REF.fullmatch(str(work_item_ref or "")):
        raise ValueError("unknown work item reference format")
    if review_intent not in _INTENTS:
        raise ValueError("invalid owner review intention")
    if provider_candidate_currently_open is not True:
        raise ValueError("current provider source did not verify candidate")
    current = now or datetime.now(timezone.utc)
    with _LOCK:
        items = _read(current)
        if work_item_ref not in items and len(items) >= _MAX_ITEMS:
            raise ValueError("local review queue full")
        created = current.astimezone(timezone.utc)
        item = {
            "work_item_ref": work_item_ref,
            "review_intent": review_intent,
            "created_at": created.isoformat(),
            "expires_at": (created + timedelta(days=max_days)).isoformat(),
            "review_status": "human_review_requested",
            "current_source_state": "unknown_until_explicit_recheck",
            "business_obligation_verified": False,
            "agent_execution_authorized": False,
        }
        items[work_item_ref] = item
        _write(items)
    return item


def remove_review_intention(work_item_ref: str, *, now: datetime | None = None) -> bool:
    if not _REF.fullmatch(str(work_item_ref or "")):
        raise ValueError("invalid work item reference")
    with _LOCK:
        items = _read(now or datetime.now(timezone.utc))
        found = work_item_ref in items
        if found:
            del items[work_item_ref]
            _write(items)
        return found


def clear_review_intentions() -> None:
    """Scrub local review intentions on Stripe source connect/disconnect."""
    with _LOCK:
        path = _path()
        try:
            path.unlink()
        except FileNotFoundError:
            pass


__all__ = [
    "list_review_intentions", "save_review_intention",
    "remove_review_intention", "clear_review_intentions",
]
