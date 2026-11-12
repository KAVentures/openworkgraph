"""R10 optional, minimal signal-attention delivery; never exports underlying evidence.

One explicitly configured recipient per local event log. Scheduled signal
checks only *queue* new reviewed states. A separate bounded sender transmits
counts-only notices on a background tick, after rechecking that every selected
state remains current, triggered, unacknowledged and unsnoozed.

No external delivery or network access is enabled by default.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit

from . import local_auth

FORMAT = "openworkgraph.signal-delivery-channels.v1"
PAYLOAD_FORMAT = "openworkgraph.signal-attention.v1"
MAX_CHANNELS = 20
MAX_PENDING = 50
MAX_SEEN = 2000
MAX_AUDIT = 20
MAX_STATES_PER_ALERT = 50
MAX_FAILURES = 5
MAX_BACKOFF_SECONDS = 3600
_LOCK = threading.RLock()
_LOG = re.compile(r"^log_[0-9a-f]{16}$")
_FP = re.compile(r"^[0-9a-f]{64}$")


class DeliveryError(ValueError):
    """Safe operator-facing error. Receiver URL and secret are never included."""


def _path() -> Path:
    # Receiver paths can hold bearer-like secrets. Keep the entire delivery
    # ledger in the application's credential directory, so an unencrypted
    # local/automated backup cannot silently include it.
    return local_auth.auth_dir() / "signal_delivery_channels.json"


def _now(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if epoch is None else epoch, timezone.utc).isoformat()


def _save(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".owg-signal-delivery-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"), sort_keys=True, ensure_ascii=True)
            out.flush()
            os.fsync(out.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _read() -> dict:
    path = _path()
    try:
        if path.is_symlink() or path.stat().st_size > 512 * 1024:
            raise DeliveryError("signal delivery configuration is unsafe or oversized")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {"version": 1, "channels": {}}
    except (OSError, ValueError, UnicodeError):
        raise DeliveryError("signal delivery configuration cannot be read safely") from None
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("channels"), dict):
        raise DeliveryError("signal delivery configuration is invalid")
    return data


def _url(value: str) -> tuple[str, str, int, str]:
    """Deny URL credentials/query, redirects, private-host network destinations."""
    if not isinstance(value, str) or not 1 <= len(value) <= 1024 or any(
            ord(ch) < 33 or ord(ch) > 126 for ch in value):
        raise DeliveryError("enter an HTTPS webhook URL without spaces, control characters or credentials")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise DeliveryError("invalid notification receiver URL") from None
    host = parsed.hostname
    if (not host or not re.fullmatch(r"[a-z0-9.-]{1,253}|::1", host) or
            parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or not parsed.path.startswith("/") or
            parsed.path.startswith("//") or "\\" in parsed.path or
            any(x in parsed.path.lower() for x in ("%0a", "%0d", "%00", "%2f%2f"))):
        raise DeliveryError("receiver URL must have a plain host and path without credentials or query")
    local = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme == "http" and not local:
        raise DeliveryError("plain HTTP receivers must be local loopback")
    if parsed.scheme not in {"http", "https"}:
        raise DeliveryError("use HTTPS or local loopback HTTP")
    if parsed.scheme == "https" and port not in (None, 443):
        raise DeliveryError("external HTTPS receivers use port 443")
    if parsed.scheme == "http" and port not in (None, 8787):
        raise DeliveryError("local HTTP test receivers use port 8787")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global and not ip.is_loopback:
            raise DeliveryError("private and reserved destination addresses are not allowed")
    return parsed.scheme, host, port or (443 if parsed.scheme == "https" else 80), parsed.path


def _audit(item: dict, notification_id: str, outcome: str, at: float) -> None:
    """Local bounded metadata only; no endpoint path, source or evidence."""
    item["audit"] = (item.get("audit", []) + [{
        "at": _now(at), "notification_id": notification_id, "outcome": outcome,
    }])[-MAX_AUDIT:]


def _public(item: dict) -> dict:
    return {
        "format": FORMAT, "configured": True,
        "channel_id": item["channel_id"], "log_id": item["log_id"],
        "recipient_host": _url(item["receiver_url"])[1],
        "enabled": item["enabled"], "created_at": item["created_at"],
        "pending_notifications": len(item["pending"]),
        "delivered_notifications": item["delivered"],
        "last_delivered_at": item.get("last_delivered_at"),
        "recent_delivery_attempts": list(item.get("audit", []))[-MAX_AUDIT:],
        "consecutive_failures": item["consecutive_failures"],
        "paused_reason": item.get("paused_reason"),
        "notification_secret_shown_again": False,
        "includes_rule_names": False, "includes_case_data": False,
        "includes_case_ids": False, "authorizes_business_actions": False,
    }


def any_enabled() -> bool:
    with _LOCK:
        return any(item["enabled"] for item in _read()["channels"].values())


def enabled_for(log_id: str) -> bool:
    with _LOCK:
        item = _read()["channels"].get(log_id)
        return bool(item and item["enabled"])


def status(log_id: str) -> dict:
    with _LOCK:
        item = _read()["channels"].get(log_id)
        if item is None:
            return {"format": FORMAT, "configured": False, "enabled": False,
                    "pending_notifications": 0, "includes_case_data": False,
                    "authorizes_business_actions": False}
        return _public(item)


def configure(log_id: str, receiver_url: str, *,
              confirm_external_delivery: bool, confirm_counts_only: bool) -> dict:
    if confirm_external_delivery is not True or confirm_counts_only is not True:
        raise DeliveryError("confirm external delivery and the counts-only disclosure separately")
    if not isinstance(log_id, str) or not _LOG.fullmatch(log_id):
        raise DeliveryError("invalid event-log identifier")
    _url(receiver_url)
    from . import local_scheduler, event_log_signal_inbox
    if not local_scheduler.scheduled("event_log_signals", log_id):
        raise DeliveryError("schedule this exact log's saved signal rules before external delivery")
    # Ensure log exists, with readable saved rules and no corrupt state.
    inbox = event_log_signal_inbox.list_inbox(log_id)
    if not inbox["items"]:
        raise DeliveryError("save at least one signal rule before delivery")
    with _LOCK:
        data = _read()
        if log_id in data["channels"]:
            raise DeliveryError("disable and remove the existing channel before creating another")
        if len(data["channels"]) >= MAX_CHANNELS:
            raise DeliveryError("maximum notification channels reached")
        secret = secrets.token_hex(32)
        item = {
            "channel_id": "ndl_" + secrets.token_hex(8), "log_id": log_id,
            "receiver_url": receiver_url, "signing_secret": secret,
            "enabled": True, "created_at": _now(),
            "pending": [], "seen": [], "audit": [], "delivered": 0,
            "last_delivered_at": None, "consecutive_failures": 0,
            "paused_reason": None,
        }
        data["channels"][log_id] = item
        _save(data)
    # The secret is returned once to a local dashboard session, never in status.
    return {**_public(item), "notification_secret": secret}


def remove(log_id: str) -> dict:
    with _LOCK:
        data = _read()
        deleted = data["channels"].pop(log_id, None) is not None
        if deleted:
            _save(data)
    return {"removed": deleted, "pending_erased": deleted, "secret_erased": deleted}


def resume(log_id: str, *, confirm_external_delivery: bool) -> dict:
    if confirm_external_delivery is not True:
        raise DeliveryError("explicit delivery reapproval is required")
    from . import local_scheduler
    if not local_scheduler.scheduled("event_log_signals", log_id):
        raise DeliveryError("signal schedule must be enabled before resuming delivery")
    with _LOCK:
        data = _read()
        item = data["channels"].get(log_id)
        if item is None:
            raise DeliveryError("unknown notification channel")
        item["enabled"] = True
        item["paused_reason"] = None
        item["consecutive_failures"] = 0
        for row in item["pending"]:
            row["next_attempt_epoch"] = time.time()
        _save(data)
        return _public(item)


def queue_current(log_id: str) -> dict:
    """Add new currently unreviewed states. No HTTP, even on failure."""
    from . import event_log_signal_inbox as inbox
    with _LOCK:
        item = _read()["channels"].get(log_id)
        if not item or not item["enabled"]:
            return {"queued": 0, "delivery_enabled": False}
        channel_id = item["channel_id"]
    rows = inbox.list_inbox(log_id)["items"]
    with _LOCK:
        data = _read()
        item = data["channels"].get(log_id)
        if not item or not item["enabled"] or item["channel_id"] != channel_id:
            return {"queued": 0, "delivery_enabled": False}
        selected, known = [], set(item["seen"])
        for message in item["pending"]:
            known.update(state["key"] for state in message["states"])
        for state in rows:
            rid, fp = state.get("rule_id"), state.get("state_fingerprint")
            if (state.get("status") != "unreviewed" or not state.get("requires_review") or
                    not isinstance(rid, str) or not isinstance(fp, str) or not _FP.fullmatch(fp)):
                continue
            key = hashlib.sha256((channel_id + rid + fp).encode("ascii")).hexdigest()
            if key not in known:
                selected.append({"rule_id": rid, "state_fingerprint": fp, "key": key})
                known.add(key)
        if not selected:
            return {"queued": 0, "delivery_enabled": True}
        if len(item["pending"]) >= MAX_PENDING or len(selected) > MAX_STATES_PER_ALERT:
            raise DeliveryError("notification queue capacity reached; review locally")
        item["pending"].append({
            "notification_id": "ntf_" + secrets.token_hex(12),
            "states": selected, "created_at": _now(),
            "next_attempt_epoch": time.time(), "attempts": 0,
        })
        _save(data)
    return {"queued": len(selected), "delivery_enabled": True}


def _valid_states(log_id: str, entry: dict) -> bool:
    from . import event_log_signal_inbox as inbox, local_scheduler
    if not local_scheduler.scheduled("event_log_signals", log_id):
        return False
    try:
        rows = inbox.list_inbox(log_id)["items"]
    except Exception:
        return False
    current = {(r["rule_id"], r["state_fingerprint"]) for r in rows
               if r.get("status") == "unreviewed" and r.get("requires_review") is True}
    return bool(entry["states"]) and all(
        (state["rule_id"], state["state_fingerprint"]) in current for state in entry["states"])


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname: str, address: str, port: int):
        super().__init__(hostname, port=port, timeout=10, context=ssl.create_default_context())
        self._verified_address = address

    def connect(self):
        self.sock = socket.create_connection((self._verified_address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(self.sock, server_hostname=self.host)


def _send(receiver_url: str, body: bytes, headers: dict[str, str]) -> int:
    scheme, host, port, path = _url(receiver_url)
    # Resolve once, validate *every* address and connect to a pinned one.
    # No proxies, cookies, credentials, CNAME-based redirects, or HTTP redirects.
    try:
        candidates = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise DeliveryError("notification receiver DNS unavailable") from None
    if not candidates:
        raise DeliveryError("notification receiver address unavailable")
    addresses = [row[4][0] for row in candidates]
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if not all((ipaddress.ip_address(addr).is_loopback if loopback
                else ipaddress.ip_address(addr).is_global) for addr in addresses):
        raise DeliveryError("receiver resolved to a restricted address")
    conn = (_PinnedHTTPS(host, addresses[0], port) if scheme == "https"
            else http.client.HTTPConnection(addresses[0], port, timeout=10))
    try:
        conn.request("POST", path, body=body, headers={
            "Content-Type": "application/json", "Host": host,
            "Cache-Control": "no-store", **headers})
        response = conn.getresponse()
        response.read(1024)  # Drain a bounded diagnostic body; do not expose it.
        return response.status
    except (TimeoutError, OSError, ssl.SSLError, http.client.HTTPException):
        raise DeliveryError("notification transport unavailable") from None
    finally:
        conn.close()


def _delivery_quiet(log_id: str, epoch: float) -> bool:
    # Delivery shares the *existing* owner's schedule quiet hours. A disabled
    # schedule cannot authorize outbound messages; an invalid quiet window
    # errs on the side of NOT sending.
    from . import local_scheduler
    try:
        jobs = local_scheduler.status()["schedules"]
        job = next((item for item in jobs if item["kind"] == "event_log_signals" and
                    item["target_id"] == log_id and item["enabled"]), None)
        if job is None:
            return False  # _valid_states will suppress a disabled schedule.
        quiet = local_scheduler._validate_quiet_hours(job.get("quiet_hours"))
        return bool(quiet and local_scheduler._is_quiet(epoch, quiet))
    except Exception:
        return True


def deliver_due(*, now: float | None = None, transport=None) -> dict:
    """Send at most one reviewed notice per tick. Every send is independently gated."""
    current = time.time() if now is None else now
    with _LOCK:
        data = _read()
        candidates = [(log_id, dict(entry), item["channel_id"]) for log_id, item in data["channels"].items()
                      if item["enabled"] for entry in item["pending"][:1]
                      if entry["next_attempt_epoch"] <= current]
    if not candidates:
        return {"attempted": 0, "sent": 0}
    log_id, entry, channel_id = sorted(candidates, key=lambda row: row[1]["next_attempt_epoch"])[0]
    if _delivery_quiet(log_id, current):
        return {"attempted": 0, "sent": 0, "deferred_quiet_hours": True}
    if not _valid_states(log_id, entry):
        with _LOCK:
            data = _read()
            channel = data["channels"].get(log_id)
            if channel and channel["channel_id"] == channel_id:
                channel["pending"] = [row for row in channel["pending"]
                                      if row["notification_id"] != entry["notification_id"]]
                _audit(channel, entry["notification_id"], "suppressed_stale_or_reviewed", current)
                _save(data)
        return {"attempted": 0, "sent": 0, "suppressed_stale_or_reviewed": True}
    payload = {
        "format": PAYLOAD_FORMAT,
        "notification_id": entry["notification_id"],
        "created_at": entry["created_at"],
        "unreviewed_signal_states": len(entry["states"]),
        "message": "Open your local OWG dashboard to review saved signal alerts.",
        # The generic `text` field is also accepted by Slack-style incoming
        # webhooks; it contains only the count, never local source identities.
        "text": "OWG: " + str(len(entry["states"])) +
                " saved signal state(s) require review on your local device.",
        "case_data_included": False, "source_identifiers_included": False,
        "execution_authorized": False,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with _LOCK:
        data = _read()
        channel = data["channels"].get(log_id)
        if (not channel or not channel["enabled"] or channel["channel_id"] != channel_id or
                not channel["pending"] or channel["pending"][0] != entry):
            return {"attempted": 0, "sent": 0, "suppressed_configuration_change": True}
        signature = "v1=" + hmac.new(bytes.fromhex(channel["signing_secret"]), body, hashlib.sha256).hexdigest()
        headers = {"X-OWG-Notification-ID": entry["notification_id"],
                   "X-OWG-Notification-Signature": signature}
        try:
            status_code = (transport or _send)(channel["receiver_url"], body, headers)
            if type(status_code) is not int or not 200 <= status_code < 300:
                raise DeliveryError("notification receiver did not acknowledge delivery")
        except Exception:
            entry["attempts"] += 1
            entry["next_attempt_epoch"] = current + min(MAX_BACKOFF_SECONDS, 60 * 2 ** (entry["attempts"] - 1))
            channel["pending"][0] = entry
            channel["consecutive_failures"] += 1
            if channel["consecutive_failures"] >= MAX_FAILURES:
                channel["enabled"] = False
                channel["paused_reason"] = "delivery_paused_after_five_failures"
            _audit(channel, entry["notification_id"], "transport_unacknowledged", current)
            _save(data)
            return {"attempted": 1, "sent": 0, "retry_pending": True,
                    "delivery_paused": not channel["enabled"]}
        channel["pending"].pop(0)
        channel["seen"] = (channel["seen"] + [state["key"] for state in entry["states"]])[-MAX_SEEN:]
        channel["delivered"] += 1
        channel["last_delivered_at"] = _now(current)
        channel["consecutive_failures"] = 0
        _audit(channel, entry["notification_id"], "receiver_acknowledged", current)
        _save(data)
        return {"attempted": 1, "sent": 1, "unreviewed_signal_states": len(entry["states"]),
                "case_data_included": False, "business_actions": False}


__all__ = ["DeliveryError", "configure", "remove", "resume", "status", "enabled_for", "any_enabled", "queue_current",
           "deliver_due", "FORMAT", "PAYLOAD_FORMAT"]
