"""Deduplicated incidents for monitored automations, with a review lifecycle (#370 R18).

Incidents group repeated problems: failed or handed-off runs by their first failing
step, and report-channel problems (rejected signatures, replay conflicts, lost
reports, version drift). Each incident is ``open``, ``acknowledged`` or ``resolved``;
a new occurrence after resolution reopens it. Triage records a human decision only:
it never re-runs an automation, replays a business write or contacts a provider.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import tempfile
import threading
from typing import Any

from .db import DATA_DIR

KINDS = ("run_failures", "run_handoffs", "signature_rejected", "replay_conflict", "lost_reports", "version_drift")
STATUSES = ("open", "acknowledged", "resolved")
MAX_INCIDENTS = 500
MAX_SAMPLES = 10
MAX_NOTES = 20
CLAIMS = {"business_writes_replayed": False, "automation_rerun": False, "provider_contacted": False,
          "reported_by_the_automation_not_verified_by_owg": True}
_LOCK = threading.RLock()


class IncidentError(ValueError):
    """Safe user-facing error."""


def _path():
    return DATA_DIR / "automation_incidents.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("incidents"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "incidents": {}}


def _write(data: dict[str, Any]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".owg-incidents-", suffix=".tmp", dir=path.parent)
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


def _incident_id(deployment_id: str, kind: str, fingerprint: str) -> str:
    return "inc_" + hashlib.sha256(f"{deployment_id}\0{kind}\0{fingerprint}".encode()).hexdigest()[:16]


def record(deployment_id: str, kind: str, fingerprint: str, *, count: int = 1, samples: list[str] = (),
           detail: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add occurrences to the matching incident, creating or reopening it."""
    if kind not in KINDS:
        raise IncidentError("unknown incident kind")
    incident_id = _incident_id(deployment_id, kind, fingerprint)
    with _LOCK:
        data = _load()
        now = _now()
        item = data["incidents"].get(incident_id)
        if item is None:
            if len(data["incidents"]) >= MAX_INCIDENTS:
                # Drop the oldest resolved incident to stay bounded; never drop open ones.
                resolved = sorted((v["last_seen"], k) for k, v in data["incidents"].items() if v["status"] == "resolved")
                if not resolved:
                    raise IncidentError("too many open incidents; resolve some first")
                del data["incidents"][resolved[0][1]]
            item = {"incident_id": incident_id, "deployment_id": deployment_id, "kind": kind,
                    "fingerprint": fingerprint, "first_seen": now, "last_seen": now, "occurrences": 0,
                    "status": "open", "reopened_count": 0, "sample_refs": [], "notes": [], "detail": {}}
            data["incidents"][incident_id] = item
        elif item["status"] == "resolved":
            item["status"] = "open"
            item["reopened_count"] += 1
        item["occurrences"] += int(count)
        item["last_seen"] = now
        item["sample_refs"] = list(dict.fromkeys([*item["sample_refs"], *samples]))[-MAX_SAMPLES:]
        if detail:
            item["detail"] = {**item["detail"], **detail}
        _write(data)
        return dict(item)


def record_runs(deployment_id: str, runs: list[dict[str, Any]]) -> list[str]:
    """Group failed and handed-off runs by their first failing or handing-off step."""
    groups: dict[tuple[str, str], list[str]] = {}
    for run in runs:
        if run["status"] not in ("failed", "handed_to_human"):
            continue
        kind = "run_failures" if run["status"] == "failed" else "run_handoffs"
        wanted = "failed" if kind == "run_failures" else "handed_to_human"
        step = next((s for s in run["steps"] if s[2] == wanted), None)
        fingerprint = ("step:" + step[1]) if step else "no_step_reported"
        groups.setdefault((kind, fingerprint), []).append(run["run_id"])
    touched = []
    for (kind, fingerprint), run_ids in sorted(groups.items()):
        touched.append(record(deployment_id, kind, fingerprint, count=len(run_ids), samples=run_ids[:MAX_SAMPLES])["incident_id"])
    return touched


def listing(status: str | None = None, deployment_id: str | None = None) -> dict[str, Any]:
    with _LOCK:
        items = list(_load()["incidents"].values())
    if status:
        items = [i for i in items if i["status"] == status]
    if deployment_id:
        items = [i for i in items if i["deployment_id"] == deployment_id]
    # Open first, then acknowledged, then resolved; most recent first within each.
    items.sort(key=lambda i: i["last_seen"], reverse=True)
    items.sort(key=lambda i: STATUSES.index(i["status"]))
    return {"incidents": items, "counts": {s: sum(i["status"] == s for i in items) for s in STATUSES}, **CLAIMS}


def triage(incident_id: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or set(payload) - {"status", "note", "expected_last_seen"}:
        raise IncidentError("triage takes status, note and expected_last_seen")
    status, note = payload.get("status"), payload.get("note", "")
    if status not in ("acknowledged", "resolved", "open"):
        raise IncidentError("status must be acknowledged, resolved or open")
    if not isinstance(note, str) or len(note) > 500 or any(ord(c) < 32 for c in note):
        raise IncidentError("a note is a single line of at most 500 characters")
    with _LOCK:
        data = _load()
        item = data["incidents"].get(incident_id)
        if item is None:
            raise IncidentError("unknown incident")
        if payload.get("expected_last_seen") is not None and payload["expected_last_seen"] != item["last_seen"]:
            raise IncidentError("new occurrences arrived since you opened this incident; review them first")
        item["status"] = status
        if note.strip():
            item["notes"] = [*item["notes"], {"at": _now(), "status": status, "note": note.strip()}][-MAX_NOTES:]
        _write(data)
        return {**item, **CLAIMS}


def forget_deployment(deployment_id: str) -> int:
    with _LOCK:
        data = _load()
        doomed = [k for k, v in data["incidents"].items() if v["deployment_id"] == deployment_id]
        for key in doomed:
            del data["incidents"][key]
        _write(data)
    return len(doomed)


__all__ = ["CLAIMS", "IncidentError", "KINDS", "forget_deployment", "listing", "record", "record_runs", "triage"]
