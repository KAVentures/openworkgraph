"""Read-only, live-verified M365 append recovery triage.

Never retries a possibly committed POST, writes a cursor or authorizes a Graph call.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .m365_outlook_delta import _delta_private_parent, _grant, _private_read
from .m365_outlook_owg_receipt import ReceiptError, _unique
from .m365_outlook_resume_gate import authorize_resume
from .m365_outlook_resume_journal import FORMAT as JOURNAL_FORMAT, _canonical
from .m365_outlook_resume_handoff import FORMAT as HANDOFF_FORMAT

FORMAT = "openworkgraph.m365-recovery-triage.v1"


def _private_object(path, limit=8192):
    return json.loads(_private_read(path, limit), object_pairs_hook=_unique)


def triage(*, tenant, mailbox, folder, grant_file, candidate, csv_path,
           receipt_file, log_id, confirm_log_id, port, session,
           journal_dir=None, handoff_dir=None, consent_local_read=False,
           confirm_removals_reviewed=False, opener=None):
    """Return a minimized action without mutating any local or remote state.

    An absent/uncertain receipt is *not* evidence that a POST did not commit.
    Only a fresh authenticated OWG readback through authorize_resume can
    establish that a verified receipt still matches current inventory.
    """
    if consent_local_read is not True or confirm_removals_reviewed is not True:
        raise ReceiptError("Separate local read and removal review required")
    if (journal_dir is None) != (handoff_dir is None):
        raise ReceiptError("Journal and handoff must be supplied together")
    _grant(Path(grant_file), tenant, mailbox, folder)
    result = {"format": FORMAT, "checkpoint_advanced": False,
              "provider_requests": 0, "owg_writes": 0,
              "automatic_retry_allowed": False, "event_level_proof": False}
    try:
        decision = authorize_resume(
            tenant=tenant, mailbox=mailbox, folder=folder, grant_file=Path(grant_file),
            candidate=Path(candidate), csv_path=Path(csv_path),
            receipt_file=Path(receipt_file), log_id=log_id,
            confirm_log_id=confirm_log_id, port=port, session=session,
            consent_local_read=True, confirm_removals_reviewed=True, opener=opener)
    except (ReceiptError, ValueError, TypeError, OSError, KeyError):
        # Includes absent receipt, uncertain POST, changed inventory and
        # revoked authorization. Never infer that an append can be retried.
        return {**result, "status": "manual_reconciliation_required",
                "next_action": "inspect_authenticated_owg_append_history_and_provider_grant"}
    if journal_dir is None:
        return {**result, "status": "receipt_verified",
                "next_action": "operator_may_separately_prepare_handoff"}
    journal_dir, handoff_dir = Path(journal_dir), Path(handoff_dir)
    _delta_private_parent(journal_dir)
    _delta_private_parent(handoff_dir)
    if journal_dir.resolve() == handoff_dir.resolve():
        raise ReceiptError("Journal and handoff directories must differ")
    identity = hashlib.sha256(_canonical({
        "candidate_sha256": decision["candidate_sha256"],
        "log_id": decision["log_id"]})).hexdigest()
    journal = journal_dir / ("resume-" + identity + ".json")
    handoff = handoff_dir / ("handoff-" + identity + ".json")
    if not journal.exists() and not handoff.exists():
        return {**result, "status": "receipt_verified",
                "next_action": "operator_may_separately_prepare_handoff"}
    try:
        j = _private_object(journal)
        h = _private_object(handoff)
        expected = {
            "candidate_sha256": decision["candidate_sha256"],
            "log_id": log_id, "events_verified_by_count": decision["events_verified_by_count"]}
        if (type(j) is not dict or type(h) is not dict
                or j.get("format") != JOURNAL_FORMAT
                or j.get("status") != "operator_resume_recorded"
                or h.get("format") != HANDOFF_FORMAT
                or h.get("status") != "next_round_handoff_prepared"
                or any(j.get(k) != v or h.get(k) != v for k, v in expected.items())
                or j.get("successor_label") != h.get("successor_label")
                or j.get("checkpoint_advanced") is not False
                or h.get("checkpoint_advanced") is not False
                or j.get("event_level_proof") is not False
                or h.get("event_level_proof") is not False
                or h.get("resume_journal_sha256") != hashlib.sha256(
                    _canonical(j) + b"\\n").hexdigest()):
            raise ReceiptError("Handoff chain mismatch")
    except (ValueError, TypeError, OSError, KeyError, UnicodeError, RecursionError):
        return {**result, "status": "manual_reconciliation_required",
                "next_action": "inspect_private_journal_and_handoff"}
    _grant(Path(grant_file), tenant, mailbox, folder)
    return {**result, "status": "handoff_verified",
            "next_action": "operator_may_separately_authorize_next_extraction"}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        p.add_argument("--" + name, required=True)
    for name in ("grant-file", "candidate", "csv-path", "receipt-file"):
        p.add_argument("--" + name, type=Path, required=True)
    for name in ("journal-dir", "handoff-dir"):
        p.add_argument("--" + name, type=Path)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--confirm-removals-reviewed", action="store_true")
    a = vars(p.parse_args(argv))
    a["session"] = os.environ.get("OWG_DASHBOARD_SESSION")
    try:
        result = triage(**a)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["status"] != "manual_reconciliation_required" else 3
    except (ReceiptError, ValueError, TypeError, OSError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "automatic_retry_allowed": False,
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
