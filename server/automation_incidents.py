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
                    "status": "open", "reopened_count": 0, "review_revision": 0,
                    "sample_refs": [], "notes": [], "detail": {}}
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


def _retained_run_groups(conn, deployment_id: str) -> dict[tuple[str, str], dict[str, Any]]:
    """Aggregate the *current* deduplicated run store, not report submissions.

    Query in bounded batches rather than reading every step or run into memory.
    A sorted, unambiguous run-ID digest detects membership changes beyond the
    ten IDs displayed to a reviewer. Nothing here changes the source runs.
    """
    cursor = conn.execute("""
        SELECT r.run_id, r.status,
               arg_min(s.activity, s.seq) FILTER (
                 WHERE s.status = CASE WHEN r.status='failed'
                                      THEN 'failed' ELSE 'handed_to_human' END
               ) AS first_problem_activity
        FROM runs r
        LEFT JOIN steps s ON s.deployment_id=r.deployment_id AND s.run_id=r.run_id
        WHERE r.deployment_id=? AND r.status IN ('failed','handed_to_human')
        GROUP BY r.run_id, r.status
        ORDER BY r.run_id
    """, [deployment_id])
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    while True:
        batch = cursor.fetchmany(1024)
        if not batch:
            break
        for run_id, status, activity in batch:
            kind = "run_failures" if status == "failed" else "run_handoffs"
            fingerprint = "step:" + activity if activity is not None else "no_step_reported"
            key = (kind, fingerprint)
            row = groups.setdefault(key, {"count": 0, "digest": hashlib.sha256(), "samples": []})
            row["count"] += 1
            # Run IDs are already validated printable identifiers by the
            # report parser; NUL cannot appear in one. Delimit to avoid ambiguity.
            row["digest"].update(run_id.encode("utf-8") + b"\x00")
            if len(row["samples"]) < MAX_SAMPLES:
                row["samples"].append(run_id)
    return {key: {"count": value["count"], "membership_sha256": value["digest"].hexdigest(),
                  "samples": value["samples"]}
            for key, value in groups.items()}


def reconcile_deployment(deployment_id: str) -> list[str]:
    """Reconcile incident counts to distinct retained runs after a committed import.

    A duplicate import cannot inflate counts or reopen a resolved incident.
    Revisions to a retained run update the current group count and identity.
    Decreases never auto-resolve a human decision; changes in membership at
    constant or increasing count reopen a resolved incident for re-review.
    Existing manual triage notes are preserved. Channel/security incident
    occurrence counters remain independent of this run-based reconciliation.
    """
    from . import automation_monitoring as monitoring

    # Keep both the database snapshot and the sidecar update serialized with
    # import and deployment deletion, including concurrent signed channels.
    with monitoring._LOCK, _LOCK:
        conn = monitoring._connect()
        try:
            monitoring._check(conn, deployment_id)
            groups = _retained_run_groups(conn, deployment_id)
        finally:
            conn.close()

        data = _load()
        items = data["incidents"]
        now = _now()
        changed_ids: list[str] = []
        changed_any = False
        empty_hash = hashlib.sha256().hexdigest()

        for (kind, fingerprint), group in sorted(groups.items()):
            incident_id = _incident_id(deployment_id, kind, fingerprint)
            current = group["count"]
            digest = group["membership_sha256"]
            item = items.get(incident_id)
            if item is None:
                if len(items) >= MAX_INCIDENTS:
                    resolved = sorted((v["last_seen"], k) for k, v in items.items()
                                      if v["status"] == "resolved")
                    if not resolved:
                        raise IncidentError("too many open incidents; resolve some first")
                    del items[resolved[0][1]]
                items[incident_id] = {
                    "incident_id": incident_id, "deployment_id": deployment_id, "kind": kind,
                    "fingerprint": fingerprint, "first_seen": now, "last_seen": now,
                    "occurrences": current, "current_run_count": current,
                    "count_basis": "distinct_retained_run_ids",
                    "run_membership_sha256": digest, "status": "open",
                    "reopened_count": 0, "review_revision": 0,
                    "sample_refs": group["samples"], "notes": [], "detail": {},
                }
                changed_ids.append(incident_id)
                changed_any = True
                continue

            previous_digest = item.get("run_membership_sha256")
            previous_count = int(item.get("current_run_count", item["occurrences"]))
            member_changed = previous_digest is not None and previous_digest != digest
            content_changed = (previous_count != current or member_changed or
                               previous_digest is None or
                               item.get("count_basis") != "distinct_retained_run_ids")
            if not content_changed:
                continue
            # Legacy incidents have no retained-run membership evidence.
            # Correct their count without mistaking that migration for a new
            # failure and reopening a human-resolved incident.
            if (member_changed and current >= previous_count and current > 0
                    and item["status"] == "resolved"):
                item["status"] = "open"
                item["reopened_count"] += 1
            item.update(occurrences=current, current_run_count=current,
                        count_basis="distinct_retained_run_ids",
                        run_membership_sha256=digest, sample_refs=group["samples"])
            if previous_digest is not None and (member_changed or previous_count != current):
                item["last_seen"] = now
            changed_ids.append(incident_id)
            changed_any = True

        # A formerly failing run can later succeed or be reclassified to a
        # different failing activity. Preserve its review history but no longer
        # present it as a currently retained failure in the old group.
        for incident_id, item in items.items():
            if (item["deployment_id"] != deployment_id or
                    item["kind"] not in ("run_failures", "run_handoffs") or
                    (item["kind"], item["fingerprint"]) in groups):
                continue
            if (item.get("current_run_count", item["occurrences"]) == 0
                    and item.get("run_membership_sha256") == empty_hash
                    and item.get("count_basis") == "distinct_retained_run_ids"):
                continue
            previous_digest = item.get("run_membership_sha256")
            item.update(occurrences=0, current_run_count=0, sample_refs=[],
                        run_membership_sha256=empty_hash,
                        count_basis="distinct_retained_run_ids")
            if previous_digest is not None:
                item["last_seen"] = now
            changed_ids.append(incident_id)
            changed_any = True

        if changed_any:
            _write(data)
        return sorted(set(changed_ids))


def record_runs(deployment_id: str, runs: list[dict[str, Any]]) -> list[str]:
    """Compatibility wrapper: count the retained DB, never repeat submitted IDs.

    ``runs`` is intentionally not authoritative. The report importer already
    committed those records and may have replaced an existing run ID.
    """
    return reconcile_deployment(deployment_id)


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
    for incident in items:
        incident.setdefault("review_revision", 0)  # pre-upgrade human reviews
    return {"incidents": items, "counts": {s: sum(i["status"] == s for i in items) for s in STATUSES}, **CLAIMS}


def triage(incident_id: str, payload: Any) -> dict[str, Any]:
    """Only apply a human review against the exact review + occurrence snapshot.

    `expected_last_seen` protects against newly observed report events.
    `expected_review_revision` separately protects two dashboard reviewers
    from silently overwriting one another even when no new report arrived.
    """
    if (not isinstance(payload, dict) or
            set(payload) - {"status", "note", "expected_last_seen", "expected_review_revision"}):
        raise IncidentError("triage takes status, note, expected_last_seen and expected_review_revision")
    expected = payload.get("expected_review_revision")
    if type(expected) is not int or expected < 0:
        raise IncidentError("exact nonnegative expected_review_revision required; refresh incidents")
    if not isinstance(payload.get("expected_last_seen"), str) or not payload["expected_last_seen"]:
        raise IncidentError("exact expected_last_seen required; refresh incidents")
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
        if payload["expected_last_seen"] != item["last_seen"]:
            raise IncidentError("new occurrences arrived since you opened this incident; review them first")
        previous_revision = item.get("review_revision", 0)
        if expected != previous_revision:
            raise IncidentError("another review changed this incident; refresh before updating it")
        trimmed_note = note.strip()
        if item["status"] != status or trimmed_note:
            item["status"] = status
            if trimmed_note:
                item["notes"] = [*item["notes"],
                                 {"at": _now(), "status": status, "note": trimmed_note}][-MAX_NOTES:]
            item["review_revision"] = previous_revision + 1
            _write(data)
        return {**item, "review_revision": item.get("review_revision", 0), **CLAIMS}


def forget_deployment(deployment_id: str) -> int:
    with _LOCK:
        data = _load()
        doomed = [k for k, v in data["incidents"].items() if v["deployment_id"] == deployment_id]
        for key in doomed:
            del data["incidents"][key]
        _write(data)
    return len(doomed)


__all__ = ["CLAIMS", "IncidentError", "KINDS", "forget_deployment", "listing", "record", "record_runs", "reconcile_deployment", "triage"]
