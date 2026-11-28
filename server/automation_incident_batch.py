"""Atomic acknowledgement of explicitly reviewed retained incident records.

Acknowledgement is not resolution, evidence verification or execution authority.
Use the existing incident lock and atomic file writer; no separate store.
"""
from hashlib import sha256
import json
import re

from . import automation_incidents as incidents

FORMAT = "openworkgraph.automation-incident-batch-ack.v1"
MAX_SELECTED = 50


def _fail(message):
    raise incidents.IncidentError(message)


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def _selection(payload, *, apply=False):
    allowed = {"selection", "note"} | ({"preview_sha256", "confirm"} if apply else set())
    if not isinstance(payload, dict) or set(payload) != allowed:
        _fail("provide exact incident selection and note; apply requires reviewed digest and confirmation")
    rows = payload["selection"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_SELECTED:
        _fail("select one to fifty open incidents")
    note = payload["note"]
    if not isinstance(note, str) or len(note) > 500 or any(ord(c) < 32 for c in note):
        _fail("a note is a single line of at most 500 characters")
    result = {}
    fields = {"incident_id", "expected_last_seen", "expected_review_revision", "expected_occurrences", "expected_membership_sha256"}
    for row in rows:
        if not isinstance(row, dict) or set(row) != fields:
            _fail("each selection needs exact incident state pins")
        iid, stamp, rev, count, member = (row[k] for k in ("incident_id", "expected_last_seen", "expected_review_revision", "expected_occurrences", "expected_membership_sha256"))
        if (not isinstance(iid, str) or not re.fullmatch(r"inc_[a-f0-9]{16}", iid) or iid in result
                or not isinstance(stamp, str) or not 1 <= len(stamp) <= 64
                or type(rev) is not int or rev < 0 or type(count) is not int or count < 0
                or (member is not None and (not isinstance(member, str) or not re.fullmatch(r"[a-f0-9]{64}", member)))):
            _fail("invalid or duplicate incident state pin")
        result[iid] = row
    if apply and (payload["confirm"] is not True or not isinstance(payload["preview_sha256"], str)
                  or not re.fullmatch(r"[a-f0-9]{64}", payload["preview_sha256"])):
        _fail("explicit acknowledgement and exact preview digest required")
    return result, note.strip()


def _prepare(data, selected, note):
    reviewed = []
    for iid, pin in sorted(selected.items()):
        item = data["incidents"].get(iid)
        if (item is None or item["status"] != "open"
                or item["last_seen"] != pin["expected_last_seen"]
                or item.get("review_revision", 0) != pin["expected_review_revision"]
                or item["occurrences"] != pin["expected_occurrences"]
                or item.get("run_membership_sha256") != pin["expected_membership_sha256"]):
            _fail("a selected incident changed or is no longer open; refresh and review all selections")
        reviewed.append({"incident_id": iid, "deployment_id": item["deployment_id"],
                         "kind": item["kind"], "fingerprint": item["fingerprint"],
                         "last_seen": item["last_seen"], "occurrences": item["occurrences"],
                         "count_basis": item.get("count_basis", "reported_occurrences"),
                         "review_revision": item.get("review_revision", 0),
                         "record_sha256": _digest(item)})
    basis = {"format": FORMAT, "action": "acknowledge_open_incidents", "note": note, "incidents": reviewed}
    return {**basis, "preview_sha256": _digest(basis), "selected_count": len(reviewed),
            "evidence_reviewed_independently_verified": False, "resolution_claimed": False,
            "execution_authorized": False, **incidents.CLAIMS}


def preview(payload):
    selected, note = _selection(payload)
    with incidents._LOCK:
        return _prepare(incidents._load(), selected, note)


def acknowledge(payload):
    selected, note = _selection(payload, apply=True)
    with incidents._LOCK:
        data = incidents._load()
        report = _prepare(data, selected, note)
        if report["preview_sha256"] != payload["preview_sha256"]:
            _fail("incident preview changed; prepare a new exact review")
        # All validation precedes mutation, and the existing writer publishes
        # one replacement file while holding the existing incident lock.
        now = incidents._now()
        for iid in sorted(selected):
            item = data["incidents"][iid]
            item["status"] = "acknowledged"
            item["review_revision"] = item.get("review_revision", 0) + 1
            if note:
                item["notes"] = [*item["notes"], {"at": now, "status": "acknowledged", "note": note}][-incidents.MAX_NOTES:]
        incidents._write(data)
        return {**report, "acknowledged_count": len(selected), "acknowledged_incident_ids": sorted(selected)}
