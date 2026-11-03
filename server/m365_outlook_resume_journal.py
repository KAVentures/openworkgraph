"""Private, one-time operator decision journal for a verified Graph delta resume.

Never contacts Graph or advances a provider cursor. A successful record is only
an auditable local authorization decision, not proof of remote synchronization.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .m365_outlook_delta import _delta_private_parent, _private_read
from .m365_outlook_resume_gate import authorize_resume
from .m365_outlook_owg_receipt import ReceiptError, _unique

FORMAT = "openworkgraph.m365-resume-journal.v1"


def _canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def record_resume(*, journal_dir: Path, successor_label: str, **gate_kwargs):
    """Validate current live OWG state before recording a unique local decision."""
    if (type(successor_label) is not str or not 1 <= len(successor_label) <= 96
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in successor_label)
            or successor_label in (".", "..")):
        raise ReceiptError("Invalid successor label")
    journal_dir = Path(journal_dir)
    _delta_private_parent(journal_dir)
    decision = authorize_resume(**gate_kwargs)
    if decision.get("status") != "resume_authorized_for_operator":
        raise ReceiptError("Resume not authorized")
    # A digest of the authorization context prevents a candidate being silently
    # reinterpreted for a different destination or successor label.
    scope = {
        "candidate_sha256": decision["candidate_sha256"],
        "log_id": decision["log_id"],
        "successor_label": successor_label,
        "events_verified_by_count": decision["events_verified_by_count"],
    }
    identity = hashlib.sha256(_canonical({
        "candidate_sha256": scope["candidate_sha256"], "log_id": scope["log_id"]
    })).hexdigest()
    target = journal_dir / ("resume-" + identity + ".json")
    entry = {"format": FORMAT, "status": "operator_resume_recorded",
             **scope, "checkpoint_advanced": False, "provider_requests": 0,
             "event_level_proof": False}
    payload = _canonical(entry) + b"\n"
    try:
        # Exclusive creation is a cross-process replay guard. No overwrite, no
        # follow of an existing symlink, and no automatic retry on write errors.
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = json.loads(_private_read(target, 8192), object_pairs_hook=_unique)
        except (ValueError, TypeError, OSError, UnicodeError):
            raise ReceiptError("Existing journal record unreadable") from None
        if type(existing) is not dict or _canonical(existing) + b"\n" != payload:
            raise ReceiptError("Conflicting prior resume decision")
        return {**entry, "idempotent_replay": True}
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            directory = os.open(journal_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        # A partial record is deliberately not removed or overwritten: an
        # operator must inspect it before any new attempt.
        raise ReceiptError("Journal write uncertain; inspect private record") from None
    return {**entry, "idempotent_replay": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id", "successor-label"):
        parser.add_argument("--" + key, required=True)
    for key in ("grant-file", "candidate", "csv-path", "receipt-file", "journal-dir"):
        parser.add_argument("--" + key, required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-read", action="store_true")
    parser.add_argument("--confirm-removals-reviewed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = record_resume(
            journal_dir=args.journal_dir, successor_label=args.successor_label,
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            grant_file=args.grant_file, candidate=args.candidate,
            csv_path=args.csv_path, receipt_file=args.receipt_file,
            log_id=args.log_id, confirm_log_id=args.confirm_log_id,
            port=args.port, session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=args.consent_local_read,
            confirm_removals_reviewed=args.confirm_removals_reviewed)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
