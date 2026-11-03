"""Explicit, durable local next-round handoff after verified Graph resume decision.

No Graph requests, credentials, provider tokens, cursor writes or automatic sync.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from .m365_outlook_delta import _delta_private_parent, _private_read
from .m365_outlook_owg_receipt import ReceiptError, _unique
from .m365_outlook_resume_journal import FORMAT as JOURNAL_FORMAT, _canonical, record_resume

FORMAT = "openworkgraph.m365-resume-handoff.v1"


def prepare_handoff(*, journal_dir: Path, handoff_dir: Path,
                    successor_label: str, **gate_kwargs):
    """Recheck live authorization, then persist a one-time next-round handoff."""
    journal_dir, handoff_dir = Path(journal_dir), Path(handoff_dir)
    _delta_private_parent(journal_dir)
    _delta_private_parent(handoff_dir)
    if journal_dir.resolve() == handoff_dir.resolve():
        raise ReceiptError("Handoff and journal directories must differ")
    decision = record_resume(journal_dir=journal_dir,
                             successor_label=successor_label, **gate_kwargs)
    identity = hashlib.sha256(_canonical({
        "candidate_sha256": decision["candidate_sha256"],
        "log_id": decision["log_id"],
    })).hexdigest()
    journal_path = journal_dir / ("resume-" + identity + ".json")
    # Re-read the actual private persisted journal, not just a return value.
    try:
        persisted = json.loads(_private_read(journal_path, 8192),
                               object_pairs_hook=_unique)
    except (ValueError, TypeError, UnicodeError, OSError, RecursionError):
        raise ReceiptError("Private resume journal unreadable") from None
    if (type(persisted) is not dict or persisted.get("format") != JOURNAL_FORMAT
            or persisted.get("status") != "operator_resume_recorded"
            or persisted.get("candidate_sha256") != decision["candidate_sha256"]
            or persisted.get("log_id") != decision["log_id"]
            or persisted.get("successor_label") != successor_label
            or persisted.get("checkpoint_advanced") is not False
            or persisted.get("provider_requests") != 0
            or persisted.get("events_verified_by_count") != decision["events_verified_by_count"]):
        raise ReceiptError("Private journal decision mismatch")
    journal_digest = hashlib.sha256(_canonical(persisted) + b"\n").hexdigest()
    entry = {
        "format": FORMAT, "status": "next_round_handoff_prepared",
        "candidate_sha256": decision["candidate_sha256"],
        "log_id": decision["log_id"],
        "successor_label": successor_label,
        "resume_journal_sha256": journal_digest,
        "events_verified_by_count": decision["events_verified_by_count"],
        "checkpoint_advanced": False, "provider_requests": 0,
        "event_level_proof": False,
        "operator_must_start_next_extraction": True,
    }
    target = handoff_dir / ("handoff-" + identity + ".json")
    payload = _canonical(entry) + b"\n"
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        try:
            existing = _private_read(target, 8192)
        except (ValueError, TypeError, OSError, UnicodeError):
            raise ReceiptError("Existing handoff unreadable") from None
        if existing != payload:
            raise ReceiptError("Conflicting next-round handoff")
        return {**entry, "idempotent_replay": True}
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            directory = os.open(handoff_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except Exception:
        raise ReceiptError("Handoff write uncertain; inspect private record") from None
    return {**entry, "idempotent_replay": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id",
                "successor-label"):
        parser.add_argument("--" + key, required=True)
    for key in ("grant-file", "candidate", "csv-path", "receipt-file",
                "journal-dir", "handoff-dir"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-read", action="store_true")
    parser.add_argument("--confirm-removals-reviewed", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = prepare_handoff(
            journal_dir=args.journal_dir, handoff_dir=args.handoff_dir,
            successor_label=args.successor_label,
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            log_id=args.log_id, confirm_log_id=args.confirm_log_id,
            grant_file=args.grant_file, candidate=args.candidate,
            csv_path=args.csv_path, receipt_file=args.receipt_file,
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
