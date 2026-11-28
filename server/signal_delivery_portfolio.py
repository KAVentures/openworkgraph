"""Local all-channel signal delivery operations; reuses the canonical outbox.

No source reads, job execution, HTTP transport, or signal review changes.
A schedule snapshot and delivery snapshot are separate observations. Delivery
eligibility is always decided by the existing sender, never by this report.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import hmac
import json
import math
import re
import time

from . import local_scheduler as scheduler
from . import signal_notification_delivery as notices

FORMAT = "openworkgraph.signal-delivery-portfolio.v1"
MAX_DISMISS_BATCH = 20
MAX_SCHEDULE_BYTES = 2 * 1024 * 1024
HEX = re.compile(r"[a-f0-9]{64}\Z")
CHANNEL = re.compile(r"ndl_[a-f0-9]{16}\Z")
LOG = re.compile(r"log_[a-f0-9]{16}\Z")
NOTIFICATION = re.compile(r"ntf_[a-f0-9]{24}\Z")
RULE = re.compile(r"sig_[a-f0-9]{16}\Z")
PAUSE_REASONS = {"owner_paused_delivery", "delivery_paused_after_five_failures",
                 "owner_paused_delivery_portfolio"}
STATUS_ORDER = ("delivery_paused", "schedule_unknown", "schedule_missing",
                "schedule_paused", "rule_approval_missing", "quiet_hours",
                "retry_wait", "head_due", "queue_empty")
AUDIT = {"transport_attempt_started", "transport_unacknowledged", "receiver_acknowledged",
         "preflight_unavailable_deferred", "superseded_partial_state_change",
         "suppressed_stale_or_reviewed", "owner_paused_delivery", "owner_dismissed_pending",
         "owner_resumed_delivery", "owner_paused_delivery_portfolio", "owner_dismissed_pending_portfolio"}


def _error():
    return notices.DeliveryError("signal delivery ledger is invalid; inspect local delivery configuration")


def _match(value, pattern):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _integer(value, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise _error()
    return value


def _epoch(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise _error()
    return value


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _validated_channels(data):
    channels = data.get("channels")
    if not isinstance(channels, dict) or len(channels) > notices.MAX_CHANNELS:
        raise _error()
    seen_channels, seen_groups = set(), set()
    for log_id, item in channels.items():
        if not isinstance(item, dict) or not _match(log_id, LOG) or item.get("log_id") != log_id:
            raise _error()
        channel = item.get("channel_id")
        if not _match(channel, CHANNEL) or channel in seen_channels or type(item.get("enabled")) is not bool:
            raise _error()
        seen_channels.add(channel)
        pending = item.get("pending")
        if not isinstance(pending, list) or len(pending) > notices.MAX_PENDING:
            raise _error()
        _integer(item.get("consecutive_failures"), maximum=notices.MAX_FAILURES)
        _integer(item.get("delivered"))
        for key, maximum in (("seen", notices.MAX_SEEN), ("dismissed", notices.MAX_DISMISSED)):
            values = item.get(key, [])
            if not isinstance(values, list) or len(values) > maximum or any(not _match(v, HEX) for v in values):
                raise _error()
        audit = item.get("audit", [])
        if not isinstance(audit, list) or len(audit) > notices.MAX_AUDIT or any(not isinstance(a, dict) for a in audit):
            raise _error()
        for entry in pending:
            if not isinstance(entry, dict):
                raise _error()
            eid = entry.get("notification_id")
            if not _match(eid, NOTIFICATION) or eid in seen_groups:
                raise _error()
            seen_groups.add(eid)
            _integer(entry.get("attempts"), maximum=1_000_000)
            _epoch(entry.get("next_attempt_epoch"))
            states = entry.get("states")
            if not isinstance(states, list) or not 1 <= len(states) <= notices.MAX_STATES_PER_ALERT:
                raise _error()
            keys = set()
            for state in states:
                if (not isinstance(state, dict) or not _match(state.get("key"), HEX)
                        or not _match(state.get("rule_id"), RULE)
                        or not _match(state.get("state_fingerprint"), HEX)
                        or state["key"] in keys):
                    raise _error()
                keys.add(state["key"])
    return channels


def _enabled_pin(channels):
    return _digest([FORMAT, sorted((key, item["channel_id"]) for key, item in channels.items() if item["enabled"])])


def _schedule_snapshot(now):
    """Distinguish unreadable schedule metadata from a genuinely absent job."""
    try:
        with scheduler._LOCK:
            path = scheduler._path()
            try:
                with path.open("rb") as stream:
                    raw = stream.read(MAX_SCHEDULE_BYTES + 1)
            except FileNotFoundError:
                return {}, "available", bool(scheduler._THREAD and scheduler._THREAD.is_alive())
            if len(raw) > MAX_SCHEDULE_BYTES:
                raise ValueError
            data = json.loads(raw)
            rows = data.get("schedules")
            if not isinstance(rows, dict) or len(rows) > scheduler.MAX_SCHEDULES:
                raise ValueError
            jobs = {}
            for row in rows.values():
                if not isinstance(row, dict):
                    raise ValueError
                if row.get("kind") != "event_log_signals":
                    continue
                target = row.get("target_id")
                if not _match(target, LOG) or target in jobs or type(row.get("enabled")) is not bool:
                    raise ValueError
                quiet = scheduler._validate_quiet_hours(row.get("quiet_hours"))
                quiet_now = bool(quiet and scheduler._is_quiet(now, quiet))
                jobs[target] = {"enabled": row["enabled"],
                                "rule_pin_present": _match(row.get("signal_rules_fingerprint"), HEX),
                                "requires_rule_reapproval": row.get("paused_reason") == "signal_rules_changed_requires_reapproval",
                                "quiet_now": quiet_now,
                                "quiet_until": scheduler._quiet_until(now, quiet) if quiet_now else None,
                                "next_run_epoch": _epoch(row["next_run_epoch"]) if row.get("next_run_epoch") is not None else None}
            return jobs, "available", bool(scheduler._THREAD and scheduler._THREAD.is_alive())
    except (OSError, ValueError, TypeError, AttributeError, OverflowError):
        return {}, "unavailable", None


def _iso(value):
    return notices._now(value) if value is not None else None


def _safe_stamp(value):
    # Timestamp strings are metadata, not trusted text; parse and reformat.
    from datetime import datetime, timezone
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.astimezone(timezone.utc).isoformat() if at.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _classification(item, job, schedule_status, now):
    if not item["enabled"]:
        return "delivery_paused"
    if schedule_status != "available":
        return "schedule_unknown"
    if job is None:
        return "schedule_missing"
    if not job["enabled"]:
        return "schedule_paused"
    if not job["rule_pin_present"] or job["requires_rule_reapproval"]:
        return "rule_approval_missing"
    if job["quiet_now"]:
        return "quiet_hours"
    if not item["pending"]:
        return "queue_empty"
    return "head_due" if item["pending"][0]["next_attempt_epoch"] <= now else "retry_wait"


def overview(*, now=None):
    now = time.time() if now is None else _epoch(now)
    jobs, schedule_status, running = _schedule_snapshot(now)
    with notices._LOCK:
        channels = _validated_channels(notices._read())
        enabled_pin = _enabled_pin(channels)
        rows = []
        for log_id, item in channels.items():
            job = jobs.get(log_id)
            state = _classification(item, job, schedule_status, now)
            groups = [{"notification_id": entry["notification_id"],
                       "queue_sha256": notices._entry_pin(item["channel_id"], entry),
                       "created_at": _safe_stamp(entry.get("created_at")),
                       "next_attempt_at": _iso(entry["next_attempt_epoch"]),
                       "attempts": entry["attempts"], "queued_state_count": len(entry["states"]),
                       "previous_delivery_may_have_arrived": bool(entry["attempts"])}
                      for entry in item["pending"]]
            audit = [a for a in item.get("audit", []) if isinstance(a.get("outcome"), str) and a["outcome"] in AUDIT]
            reason = item.get("paused_reason")
            rows.append({"log_id": log_id, "channel_id": item["channel_id"], "enabled": item["enabled"],
                         "status": state, "paused_reason": reason if isinstance(reason, str) and reason in PAUSE_REASONS else None,
                         "pending_groups": groups, "pending_group_count": len(groups),
                         "pending_state_occurrences": sum(g["queued_state_count"] for g in groups),
                         "previously_attempted_groups": sum(g["previous_delivery_may_have_arrived"] for g in groups),
                         "consecutive_failures": item["consecutive_failures"],
                         "recorded_receiver_acknowledgements": item["delivered"],
                         "last_delivered_at": _safe_stamp(item.get("last_delivered_at")),
                         "last_retained_audit_outcome": audit[-1]["outcome"] if audit else None,
                         "last_retained_audit_at": _safe_stamp(audit[-1].get("at")) if audit else None,
                         "schedule_enabled": job["enabled"] if job else (False if schedule_status == "available" else None),
                         "schedule_next_at": _iso(job["next_run_epoch"]) if job else None,
                         "quiet_until": _iso(job["quiet_until"]) if job else None,
                         "dismissal_capacity_remaining": notices.MAX_DISMISSED - len(item.get("dismissed", []))})
    rows.sort(key=lambda r: (STATUS_ORDER.index(r["status"]), r["log_id"]))
    counts = dict.fromkeys(STATUS_ORDER, 0)
    counts.update(Counter(row["status"] for row in rows))
    return {"format": FORMAT, "observed_at": _iso(now),
            "schedule_metadata_status": schedule_status, "scheduler_running_at_snapshot": running,
            "enabled_channels_sha256": enabled_pin,
            "configured_channels": len(rows), "enabled_channels": sum(r["enabled"] for r in rows),
            "status_counts": counts, "pending_groups": sum(r["pending_group_count"] for r in rows),
            "pending_state_occurrences": sum(r["pending_state_occurrences"] for r in rows),
            "previously_attempted_groups": sum(r["previously_attempted_groups"] for r in rows),
            "channels": rows, "source_data_read": False, "notifications_sent": False,
            "actions_authorized": False,
            "interpretation": "Local queue and schedule snapshots are separate. A due head is not a promise of delivery; "
                               "the sender independently checks current evaluated state, enabled schedule and quiet hours; "
                              "the portfolio does not verify the current rule-definition digest. "
                              "Queued counts may include stale or repeated states. A receiver acknowledgement is not human review."}


def pause_all(*, expected_enabled_channels_sha256, confirm_pause):
    if confirm_pause is not True or not _match(expected_enabled_channels_sha256, HEX):
        raise notices.DeliveryError("explicit pause confirmation and exact enabled-channel digest required")
    with notices._LOCK:
        data = notices._read()
        channels = _validated_channels(data)
        if not hmac.compare_digest(_enabled_pin(channels), expected_enabled_channels_sha256):
            raise notices.DeliveryError("enabled channels changed; refresh the portfolio")
        changed = [item for item in channels.values() if item["enabled"]]
        now = time.time()
        for item in changed:
            item["enabled"] = False
            item["paused_reason"] = "owner_paused_delivery_portfolio"
            notices._audit(item, item["channel_id"], "owner_paused_delivery_portfolio", now)
        if changed:
            notices._save(data)
        return {"paused_channels": len(changed), "remaining_enabled_channels": 0,
                "retained_pending_groups": sum(len(item["pending"]) for item in channels.values()),
                "signal_reviews_changed": False, "schedules_changed": False,
                "notifications_sent": False, "actions_authorized": False}


def dismiss_batch(*, selections, confirm_dismiss):
    if confirm_dismiss is not True or not isinstance(selections, list) or not 1 <= len(selections) <= MAX_DISMISS_BATCH:
        raise notices.DeliveryError("explicitly confirm 1 to 20 exact paused notification groups")
    pairs = set()
    for row in selections:
        if (not isinstance(row, dict) or set(row) != {"log_id", "channel_id", "notification_id", "queue_sha256"}
                or not _match(row["log_id"], LOG) or not _match(row["channel_id"], CHANNEL)
                or not _match(row["notification_id"], NOTIFICATION) or not _match(row["queue_sha256"], HEX)):
            raise notices.DeliveryError("invalid exact notification selection")
        identity = row["log_id"], row["notification_id"]
        if identity in pairs:
            raise notices.DeliveryError("duplicate notification selection")
        pairs.add(identity)
    with notices._LOCK:
        data = notices._read()
        channels = _validated_channels(data)
        prepared = []
        suppression = {}
        for row in selections:
            item = channels.get(row["log_id"])
            if item is None or item["channel_id"] != row["channel_id"] or item["enabled"]:
                raise notices.DeliveryError("selected channel changed or is not paused; refresh the portfolio")
            entry = next((e for e in item["pending"] if e["notification_id"] == row["notification_id"]), None)
            if entry is None or not hmac.compare_digest(notices._entry_pin(item["channel_id"], entry), row["queue_sha256"]):
                raise notices.DeliveryError("selected notification changed; refresh the portfolio")
            keys = suppression.setdefault(row["log_id"], dict.fromkeys(item.get("dismissed", [])))
            keys.update(dict.fromkeys(state["key"] for state in entry["states"]))
            if len(keys) > notices.MAX_DISMISSED:
                raise notices.DeliveryError("dismissal capacity reached; no selected groups were changed")
            prepared.append((item, entry))
        now = time.time()
        for item, entry in prepared:
            item["dismissed"] = list(suppression[item["log_id"]])
            item["pending"].remove(entry)
            notices._audit(item, entry["notification_id"], "owner_dismissed_pending_portfolio", now)
        notices._save(data)
        return {"dismissed_groups": len(prepared),
                "affected_channels": len(suppression),
                "dismissed_state_occurrences": sum(len(entry["states"]) for _, entry in prepared),
                "previously_attempted_groups": sum(bool(entry["attempts"]) for _, entry in prepared),
                "signal_reviews_changed": False, "schedules_changed": False,
                "notifications_sent": False, "actions_authorized": False}
