"""Signed, sequenced run-report channels for monitored automations (#370 R18).

A channel lets a customer-run automation push run reports to one monitoring
deployment without a dashboard session. The dashboard creates the channel and
shows its secret once. Every report must carry a fresh timestamp, a sequence
number and an HMAC signature (``shared.automation_report_signing``), on top of the
local API token that every ``/v1`` request already needs.

OWG then:

* refuses bad signatures and stale timestamps, recording a ``signature_rejected`` incident;
* acknowledges an exact resend of a sequence number as a duplicate without storing it again;
* refuses different bytes under a used sequence number as a ``replay_conflict``;
* records skipped sequence numbers as lost reports until they arrive;
* records each (version label, workflow spec hash) it sees, and refuses a report
  for a different spec as ``version_drift`` instead of silently rebinding.

A report is still what the automation says happened; nothing here verifies
execution or outcomes, and nothing re-runs or replays anything.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import secrets
import tempfile
import threading
from typing import Any

from shared import automation_report_signing as signing

from . import automation_incidents as incidents
from . import automation_monitoring as monitoring
from .db import DATA_DIR

ENVELOPE = "openworkgraph.signed-run-report.v1"
REPLAY_WINDOW = 2000
MAX_MISSING = 1000
MAX_VERSIONS = 50
MAX_CHANNELS = 50
_LOCK = threading.RLock()


class ChannelError(ValueError):
    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


def _path():
    return DATA_DIR / "automation_report_channels.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("channels"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "channels": {}}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-channels-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, separators=(",", ":"))
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


def _public(channel: dict[str, Any]) -> dict[str, Any]:
    return {"channel_id": channel["channel_id"], "deployment_id": channel["deployment_id"],
            "created_at": channel["created_at"], "rotated_at": channel.get("rotated_at"),
            "revoked_at": channel.get("revoked_at"), "highest_sequence": channel["highest_sequence"],
            "missing_sequences": channel["missing"][:50], "missing_count": len(channel["missing"]),
            "missing_truncated": channel.get("missing_truncated", False),
            "versions": sorted(channel["versions"].values(), key=lambda v: v["first_seen"]),
            "accepted_reports": channel.get("accepted_reports", 0), "refused_reports": channel.get("refused_reports", 0),
            "secret_shown_again": False}


def _deployment_exists(deployment_id: str) -> bool:
    return any(d["deployment_id"] == deployment_id for d in monitoring.deployment_references())


def create(deployment_id: str) -> dict[str, Any]:
    """A new channel for an existing deployment. The secret is returned only here."""
    if not _deployment_exists(deployment_id):
        raise ChannelError("unknown deployment", 404)
    with _LOCK:
        data = _load()
        live = [c for c in data["channels"].values() if not c.get("revoked_at")]
        if len(data["channels"]) >= MAX_CHANNELS:
            raise ChannelError("too many report channels; revoke and delete one first", 409)
        if any(c["deployment_id"] == deployment_id for c in live):
            raise ChannelError("this deployment already has a channel; rotate its secret instead", 409)
        channel_id = "chn_" + secrets.token_hex(8)
        secret = secrets.token_hex(32)
        data["channels"][channel_id] = {"channel_id": channel_id, "deployment_id": deployment_id, "secret": secret,
                                        "created_at": _now(), "highest_sequence": 0, "received": {}, "missing": [],
                                        "versions": {}}
        _write(data)
        return {**_public(data["channels"][channel_id]), "secret": secret, "secret_shown_again": False,
                "signing": "HMAC-SHA256 over '{timestamp}.{sequence}.' + body; see shared/automation_report_signing.py"}


def rotate(channel_id: str) -> dict[str, Any]:
    """New secret; sequence numbers and history continue."""
    with _LOCK:
        data = _load()
        channel = data["channels"].get(channel_id)
        if channel is None or channel.get("revoked_at"):
            raise ChannelError("unknown or revoked channel", 404)
        channel["secret"] = secrets.token_hex(32)
        channel["rotated_at"] = _now()
        _write(data)
        return {**_public(channel), "secret": channel["secret"]}


def revoke(channel_id: str) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        channel = data["channels"].get(channel_id)
        if channel is None:
            raise ChannelError("unknown channel", 404)
        channel["revoked_at"] = channel.get("revoked_at") or _now()
        channel["secret"] = ""
        _write(data)
        return _public(channel)


def listing(deployment_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        channels = list(_load()["channels"].values())
    if deployment_id:
        channels = [c for c in channels if c["deployment_id"] == deployment_id]
    return {"channels": [_public(c) for c in channels], "replay_window": REPLAY_WINDOW,
            "max_clock_skew_seconds": signing.MAX_SKEW_SECONDS, **incidents.CLAIMS}


def forget_deployment(deployment_id: str) -> int:
    with _LOCK:
        data = _load()
        doomed = [k for k, c in data["channels"].items() if c["deployment_id"] == deployment_id]
        for key in doomed:
            del data["channels"][key]
        _write(data)
    return len(doomed)


def _outcome(channel: dict[str, Any], sequence: int, digest: str, outcome: str, status: int, detail: str) -> None:
    channel["received"][str(sequence)] = {"sha256": digest, "at": _now(), "outcome": outcome, "status": status,
                                          "detail": detail}
    if len(channel["received"]) > REPLAY_WINDOW:
        for key in sorted(channel["received"], key=int)[:len(channel["received"]) - REPLAY_WINDOW]:
            del channel["received"][key]


def _track_sequence(channel: dict[str, Any], sequence: int) -> int:
    """Update gaps; returns how many sequence numbers this report newly showed as missing."""
    new_gaps = 0
    if sequence in channel["missing"]:
        channel["missing"].remove(sequence)
    elif sequence > channel["highest_sequence"] + 1:
        skipped = list(range(channel["highest_sequence"] + 1, sequence))
        new_gaps = len(skipped)
        channel["missing"].extend(skipped)
        if len(channel["missing"]) > MAX_MISSING:
            channel["missing"] = channel["missing"][-MAX_MISSING:]
            channel["missing_truncated"] = True
    channel["highest_sequence"] = max(channel["highest_sequence"], sequence)
    return new_gaps


def ingest(channel_id: str, timestamp: str, sequence_header: str, signature: str, body: bytes) -> dict[str, Any]:
    with _LOCK:
        data = _load()
        channel = data["channels"].get(channel_id)
        if channel is None:
            raise ChannelError("unknown report channel", 404)
        if channel.get("revoked_at"):
            raise ChannelError("this report channel is revoked", 403)
        deployment_id = channel["deployment_id"]
        reason = signing.verify(channel["secret"], timestamp, sequence_header, signature, body)
        if reason is not None:
            channel["refused_reports"] = channel.get("refused_reports", 0) + 1
            _write(data)
            incidents.record(deployment_id, "signature_rejected", reason, detail={"channel_id": channel_id})
            raise ChannelError("report signature or timestamp was not accepted", 401)
        sequence = int(sequence_header)
        digest = hashlib.sha256(body).hexdigest()
        seen = channel["received"].get(str(sequence))
        if seen is not None:
            if seen["sha256"] == digest:
                # An exact resend (for example after a lost response): same answer, nothing stored again.
                return {"duplicate": True, "sequence": sequence, "outcome": seen["outcome"], "stored_again": False,
                        **incidents.CLAIMS}
            incidents.record(deployment_id, "replay_conflict", f"sequence:{sequence}",
                             detail={"channel_id": channel_id})
            channel["refused_reports"] = channel.get("refused_reports", 0) + 1
            _write(data)
            raise ChannelError("this sequence number was already used for a different report", 409)
        if sequence <= channel["highest_sequence"] - REPLAY_WINDOW:
            raise ChannelError("sequence number is outside the replay window", 409)
        try:
            envelope = json.loads(body)
            if (not isinstance(envelope, dict) or envelope.get("format") != ENVELOPE
                    or set(envelope) - {"format", "version_label", "report"}):
                raise ValueError
            label = envelope.get("version_label", "")
            if not isinstance(label, str) or len(label) > 64 or any(ord(c) < 32 for c in label):
                raise ValueError
            parsed = monitoring.parse_report(envelope.get("report"))
        except (ValueError, monitoring.MonitoringError):
            _outcome(channel, sequence, digest, "refused_invalid", 422, "invalid report")
            channel["refused_reports"] = channel.get("refused_reports", 0) + 1
            _track_sequence(channel, sequence)
            _write(data)
            raise ChannelError(f"the body must be a valid {ENVELOPE} envelope") from None
        try:
            result = monitoring.import_report(envelope["report"], deployment_id)
        except monitoring.MonitoringError as exc:
            drift = "different workflow spec" in str(exc)
            if drift:
                incidents.record(deployment_id, "version_drift", f"spec:{parsed['spec_hash'] or 'none'}",
                                 detail={"channel_id": channel_id, "version_label": label})
            _outcome(channel, sequence, digest, "refused_version_drift" if drift else "refused_invalid",
                     409 if drift else 422, str(exc)[:200])
            channel["refused_reports"] = channel.get("refused_reports", 0) + 1
            _track_sequence(channel, sequence)
            _write(data)
            raise ChannelError(str(exc), 409 if drift else 422) from None
        gaps = _track_sequence(channel, sequence)
        key = f"{label}\0{parsed['spec_hash'] or ''}"
        version = channel["versions"].get(key)
        if version is None and len(channel["versions"]) < MAX_VERSIONS:
            version = channel["versions"][key] = {"version_label": label, "workflow_spec_sha256": parsed["spec_hash"],
                                                  "first_seen": _now(), "reports": 0, "runs": 0}
        if version is not None:
            version.update(last_seen=_now(), reports=version["reports"] + 1, runs=version["runs"] + len(parsed["runs"]))
        _outcome(channel, sequence, digest, "imported", 200, "")
        channel["accepted_reports"] = channel.get("accepted_reports", 0) + 1
        _write(data)
    if gaps:
        incidents.record(deployment_id, "lost_reports", f"channel:{channel_id}", count=gaps,
                         detail={"channel_id": channel_id})
    touched = incidents.reconcile_deployment(deployment_id)
    return {"duplicate": False, "sequence": sequence, "outcome": "imported", "runs_imported": result["runs_imported"],
            "new_missing_sequences": gaps, "incidents_updated": touched, **incidents.CLAIMS}


__all__ = ["ChannelError", "ENVELOPE", "create", "forget_deployment", "ingest", "listing", "revoke", "rotate"]
