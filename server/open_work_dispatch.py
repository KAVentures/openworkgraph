from __future__ import annotations

"""Owner-offered Open Work hand-off to external agents (roadmap #247).

The owner turns dispatch on, then explicitly offers individual cases already in
the local review queue (``open_work_reviews``) to agents. Connected agents can
list offers, claim one under a short lease so two agents never work the same
case, and report what they did. A report is recorded as the agent's own,
unverified claim: completion is only established by the existing independent
``verify_case_operation`` source check.

This module grants no action authority. Agents act only through their own,
separately authorized business tools. Offers carry the opaque ``owg:w:`` case
reference and the owner's review intent, never provider IDs, customer data or
credentials. Offers expire with the underlying review intention, or after at
most seven days; claims expire after the lease. Turning dispatch off withdraws
every offer at once.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import secrets
import stat
import tempfile
import threading
from typing import Any

from .local_auth import auth_dir
from .open_work_reviews import list_review_intentions

FORMAT = "openworkgraph.open-work-dispatch.v1"
_FILENAME = ".open_work_dispatch.json"
_REF = re.compile(r"^owg:w:[0-9a-f]{32}$")
_AGENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:-]{0,63}$")
_RECEIPT = re.compile(r"^owg:(?:op|rcpt|w|r):[0-9a-f]{16,64}$")
_OUTCOMES = frozenset({"attempted", "declined", "needs_human"})
_MAX_OFFERS = 50
_MAX_NOTE = 280
_MAX_HISTORY = 20
_MAX_FILE_BYTES = 131_072
_DEFAULT_LEASE_MINUTES = 30
_MAX_LEASE_MINUTES = 240
_MAX_OFFER_DAYS = 7
_LOCK = threading.RLock()


class DispatchError(ValueError):
    """Safe error; never echoes stored content."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path() -> Path:
    return auth_dir() / _FILENAME


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _empty() -> dict[str, Any]:
    return {"version": 1, "enabled": False, "lease_minutes": _DEFAULT_LEASE_MINUTES, "offers": {}}


def _read() -> dict[str, Any]:
    path = _path()
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
            return _empty()
        if os.name == "posix" and info.st_mode & 0o077:
            return _empty()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _empty()
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("offers"), dict):
        return _empty()
    state = _empty()
    state["enabled"] = raw.get("enabled") is True
    lease = raw.get("lease_minutes")
    if type(lease) is int and 5 <= lease <= _MAX_LEASE_MINUTES:
        state["lease_minutes"] = lease
    for ref, offer in list(raw["offers"].items())[:_MAX_OFFERS]:
        if _REF.fullmatch(str(ref)) and isinstance(offer, dict):
            state["offers"][ref] = offer
    return state


def _write(state: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise OSError("unsafe dispatch file path")
    serialized = json.dumps(state, separators=(",", ":")) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_FILE_BYTES:
        raise DispatchError("dispatch queue exceeds its local storage limit")
    fd, temp = tempfile.mkstemp(prefix=".owg-dispatch-", suffix=".tmp", dir=path.parent)
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


def _reviews() -> dict[str, dict[str, Any]]:
    return {item["work_item_ref"]: item for item in list_review_intentions()["items"]}


def _sweep(state: dict[str, Any], now: datetime) -> bool:
    """Drop offers whose review intention is gone/expired and release stale claims."""
    changed = False
    reviews = _reviews()
    for ref, offer in list(state["offers"].items()):
        expires = _time(offer.get("expires_at"))
        if ref not in reviews or expires is None or expires <= now:
            del state["offers"][ref]
            changed = True
            continue
        claim = offer.get("claim")
        if isinstance(claim, dict):
            until = _time(claim.get("lease_expires_at"))
            if until is None or until <= now:
                offer["claim"] = None
                offer["status"] = "offered"
                offer.setdefault("history", []).append({"at": now.isoformat(), "event": "lease_expired"})
                offer["history"] = offer["history"][-_MAX_HISTORY:]
                changed = True
    return changed


def _public(offer: dict[str, Any], *, owner: bool) -> dict[str, Any]:
    claim = offer.get("claim") if isinstance(offer.get("claim"), dict) else None
    out = {
        "work_item_ref": offer["work_item_ref"],
        "review_intent": offer["review_intent"],
        "status": offer["status"],
        "offered_at": offer["offered_at"],
        "expires_at": offer["expires_at"],
        "owner_note": offer.get("owner_note", ""),
        "claimed_by": claim["agent"] if claim else None,
        "lease_expires_at": claim["lease_expires_at"] if claim else None,
        "last_report": offer.get("last_report"),
        "agent_report_is_unverified": True,
        "business_obligation_verified": False,
        "agent_execution_authorized": False,
        "verify_with": "verify_case_operation",
    }
    if owner:
        out["history"] = list(offer.get("history", []))
    return out


def status(*, owner: bool = True) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        state = _read()
        if _sweep(state, now):
            _write(state)
    offers = sorted(state["offers"].values(), key=lambda o: o["offered_at"])
    return {
        "format": FORMAT,
        "enabled": state["enabled"],
        "lease_minutes": state["lease_minutes"],
        "offers": [_public(o, owner=owner) for o in offers] if state["enabled"] or owner else [],
        "grants_action_authority": False,
        "agent_reports_are_unverified_claims": True,
        "local_only": True,
    }


def set_enabled(enabled: bool, *, lease_minutes: int | None = None) -> dict[str, Any]:
    if type(enabled) is not bool:
        raise DispatchError("enabled must be true or false")
    with _LOCK:
        state = _read()
        state["enabled"] = enabled
        if lease_minutes is not None:
            if type(lease_minutes) is not int or not 5 <= lease_minutes <= _MAX_LEASE_MINUTES:
                raise DispatchError(f"lease_minutes must be 5-{_MAX_LEASE_MINUTES}")
            state["lease_minutes"] = lease_minutes
        if not enabled:
            state["offers"] = {}  # turning dispatch off withdraws every offer
        _write(state)
    return status()


def offer(work_item_ref: str, *, owner_note: str = "", days: int = 3) -> dict[str, Any]:
    """Owner action: offer one queued review case to connected agents."""
    if not _REF.fullmatch(str(work_item_ref or "")):
        raise DispatchError("invalid work item reference")
    if not isinstance(owner_note, str) or len(owner_note) > _MAX_NOTE or any(ord(c) < 32 for c in owner_note):
        raise DispatchError(f"owner note must be plain text of at most {_MAX_NOTE} characters")
    if type(days) is not int or not 1 <= days <= _MAX_OFFER_DAYS:
        raise DispatchError(f"days must be 1-{_MAX_OFFER_DAYS}")
    now = _now()
    with _LOCK:
        state = _read()
        if not state["enabled"]:
            raise DispatchError("turn on agent dispatch first")
        review = _reviews().get(work_item_ref)
        if review is None:
            raise DispatchError("only cases in the local review queue can be offered")
        if work_item_ref not in state["offers"] and len(state["offers"]) >= _MAX_OFFERS:
            raise DispatchError("dispatch queue is full")
        review_expiry = _time(review["expires_at"]) or now
        expires = min(now + timedelta(days=days), review_expiry)
        state["offers"][work_item_ref] = {
            "work_item_ref": work_item_ref,
            "review_intent": review["review_intent"],
            "status": "offered",
            "offered_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "owner_note": owner_note.strip(),
            "claim": None,
            "last_report": None,
            "history": [{"at": now.isoformat(), "event": "offered"}],
        }
        _write(state)
        return _public(state["offers"][work_item_ref], owner=True)


def withdraw(work_item_ref: str) -> bool:
    with _LOCK:
        state = _read()
        removed = state["offers"].pop(str(work_item_ref), None) is not None
        if removed:
            _write(state)
    return removed


def _agent(agent: Any) -> str:
    if not isinstance(agent, str) or not _AGENT.fullmatch(agent.strip()):
        raise DispatchError("agent label must be 1-64 letters, digits, spaces or ._:-")
    return agent.strip()


def claim(work_item_ref: str, agent: str) -> dict[str, Any]:
    """Agent action: take an offered case under a lease; returns a lease token."""
    agent = _agent(agent)
    now = _now()
    with _LOCK:
        state = _read()
        _sweep(state, now)
        if not state["enabled"]:
            raise DispatchError("agent dispatch is switched off")
        item = state["offers"].get(str(work_item_ref))
        if item is None:
            raise DispatchError("this case is not offered to agents")
        if item.get("claim"):
            raise DispatchError("this case is already claimed by another agent")
        if item["status"] == "reported":
            raise DispatchError("this case already has an agent report awaiting owner review")
        token = secrets.token_urlsafe(24)
        until = now + timedelta(minutes=state["lease_minutes"])
        item["claim"] = {"agent": agent, "token": token, "claimed_at": now.isoformat(),
                         "lease_expires_at": until.isoformat()}
        item["status"] = "claimed"
        item["history"] = (item.get("history", []) + [{"at": now.isoformat(), "event": "claimed", "agent": agent}])[-_MAX_HISTORY:]
        _write(state)
        return {**_public(item, owner=False), "lease_token": token}


def _claimed(state: dict[str, Any], work_item_ref: str, token: str) -> dict[str, Any]:
    item = state["offers"].get(str(work_item_ref))
    claim_ = item.get("claim") if item else None
    if not claim_ or not isinstance(token, str) or not secrets.compare_digest(claim_["token"], token):
        raise DispatchError("no active claim for this case and lease token")
    return item


def release(work_item_ref: str, lease_token: str) -> dict[str, Any]:
    now = _now()
    with _LOCK:
        state = _read()
        _sweep(state, now)
        item = _claimed(state, work_item_ref, lease_token)
        agent = item["claim"]["agent"]
        item["claim"], item["status"] = None, "offered"
        item["history"] = (item["history"] + [{"at": now.isoformat(), "event": "released", "agent": agent}])[-_MAX_HISTORY:]
        _write(state)
        return _public(item, owner=False)


def report(work_item_ref: str, lease_token: str, outcome: str, *,
           note: str = "", operation_receipt_ref: str | None = None) -> dict[str, Any]:
    """Agent action: record what it did. Stored as an unverified agent claim."""
    if outcome not in _OUTCOMES:
        raise DispatchError("outcome must be attempted, declined or needs_human")
    if not isinstance(note, str) or len(note) > _MAX_NOTE or any(ord(c) < 32 and c not in "\n" for c in note):
        raise DispatchError(f"note must be plain text of at most {_MAX_NOTE} characters")
    if operation_receipt_ref is not None and not _RECEIPT.fullmatch(str(operation_receipt_ref)):
        raise DispatchError("operation_receipt_ref must be an opaque OWG reference")
    now = _now()
    with _LOCK:
        state = _read()
        _sweep(state, now)
        item = _claimed(state, work_item_ref, lease_token)
        agent = item["claim"]["agent"]
        item["last_report"] = {
            "agent": agent, "reported_at": now.isoformat(), "outcome": outcome,
            "note": note.strip(), "operation_receipt_ref": operation_receipt_ref,
            "verified": False,
        }
        item["claim"] = None
        # A decline returns the case to the pool; anything else waits for the owner.
        item["status"] = "offered" if outcome == "declined" else "reported"
        item["history"] = (item["history"] + [{"at": now.isoformat(), "event": f"reported_{outcome}", "agent": agent}])[-_MAX_HISTORY:]
        _write(state)
        return _public(item, owner=False)


def acknowledge(work_item_ref: str, *, reoffer: bool) -> dict[str, Any] | None:
    """Owner action after reviewing a report: re-offer the case or close the offer."""
    now = _now()
    with _LOCK:
        state = _read()
        item = state["offers"].get(str(work_item_ref))
        if item is None:
            raise DispatchError("this case is not offered")
        if not reoffer:
            del state["offers"][str(work_item_ref)]
            _write(state)
            return None
        item["status"], item["claim"] = "offered", None
        # A re-offer is a new owner approval generation; old grants stay denied.
        item["offered_at"] = now.isoformat()
        item["last_report"] = None
        item["history"] = (item["history"] + [{"at": now.isoformat(), "event": "reoffered"}])[-_MAX_HISTORY:]
        _write(state)
        return _public(item, owner=True)


def clear() -> None:
    with _LOCK:
        try:
            _path().unlink(missing_ok=True)
        except OSError:
            pass


__all__ = ["DispatchError", "acknowledge", "claim", "clear", "offer", "release", "report",
           "set_enabled", "status", "withdraw"]
