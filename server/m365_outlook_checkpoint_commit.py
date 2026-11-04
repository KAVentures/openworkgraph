"""Explicit, owner-private commit of a reviewed Microsoft Graph delta checkpoint.

No Graph requests or OWG writes. A checkpoint is a local operator decision, not
proof of provider acceptance or individual event membership.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .m365_outlook_delta import _candidate, _delta_private_parent, _grant, _private_read, _scope
from .m365_outlook_owg_receipt import ReceiptError, _artifact, _unique
from .m365_outlook_resume_gate import authorize_resume
from .m365_outlook_resume_journal import _canonical
from .m365_outlook_verified_append_receipt import _exclusive_private

FORMAT = "openworkgraph.m365-operator-checkpoint.v1"


def commit_checkpoint(*, tenant, mailbox, folder, grant_file: Path,
                      candidate: Path, csv_path: Path, receipt_file: Path,
                      checkpoint_file: Path, log_id, confirm_log_id, port=8765,
                      session=None, consent_local_read=False,
                      confirm_removals_reviewed=False,
                      consent_checkpoint_commit=False, opener=None):
    """Persist a validated cursor once, only after live OWG receipt verification."""
    if consent_checkpoint_commit is not True:
        raise ReceiptError("Explicit checkpoint commit consent required")
    candidate, csv_path = Path(candidate), Path(csv_path)
    receipt_file, checkpoint_file = Path(receipt_file), Path(checkpoint_file)
    grant_file = Path(grant_file)
    _delta_private_parent(checkpoint_file.parent)
    if (checkpoint_file.parent != candidate.parent or
            checkpoint_file.suffix != ".json" or
            not checkpoint_file.name.startswith("checkpoint-") or
            any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                for c in checkpoint_file.name) or
            checkpoint_file in (candidate, csv_path, receipt_file, grant_file)):
        raise ReceiptError("Invalid checkpoint destination")
    # Existing checkpoints are never overwritten, including symlinks and partial files.
    if checkpoint_file.exists() or checkpoint_file.is_symlink():
        raise ReceiptError("Checkpoint already exists; inspect before proceeding")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    _, artifact = _artifact(candidate, csv_path, scope, folder)
    cursor = _candidate(candidate, scope, folder, True)
    decision = authorize_resume(
        tenant=tenant, mailbox=mailbox, folder=folder,
        grant_file=grant_file, candidate=candidate, csv_path=csv_path,
        receipt_file=receipt_file, log_id=log_id,
        confirm_log_id=confirm_log_id, port=port, session=session,
        consent_local_read=consent_local_read,
        confirm_removals_reviewed=confirm_removals_reviewed, opener=opener,
    )
    if decision.get("status") != "resume_authorized_for_operator":
        raise ReceiptError("Prior append not verified")
    _grant(grant_file, tenant, mailbox, folder)
    # Bind the checkpoint to the exact reviewed candidate, destination and scope.
    entry = {
        "format": FORMAT, "status": "operator_checkpoint_committed",
        "scope_sha256": scope, "log_id": log_id,
        "candidate_sha256": hashlib.sha256(_private_read(candidate, 32768)).hexdigest(),
        "csv_sha256": artifact["csv_sha256"], "cursor_url": cursor,
        "removal_markers_reviewed_by_operator": True,
        "event_level_proof": False, "provider_requests": 0,
        "owg_write_requests": 0,
    }
    try:
        _exclusive_private(checkpoint_file, _canonical(entry) + b"\n")
    except FileExistsError:
        raise ReceiptError("Checkpoint already committed") from None
    # Never return the bearer-like Graph continuation URL to stdout.
    return {"format": FORMAT, "status": "checkpoint_committed",
            "log_id": log_id, "candidate_sha256": entry["candidate_sha256"],
            "event_level_proof": False, "provider_requests": 0,
            "owg_write_requests": 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        parser.add_argument("--" + key, required=True)
    for key in ("grant-file", "candidate", "csv-path", "receipt-file", "checkpoint-file"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-read", action="store_true")
    parser.add_argument("--confirm-removals-reviewed", action="store_true")
    parser.add_argument("--consent-checkpoint-commit", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = commit_checkpoint(
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            grant_file=args.grant_file, candidate=args.candidate,
            csv_path=args.csv_path, receipt_file=args.receipt_file,
            checkpoint_file=args.checkpoint_file, log_id=args.log_id,
            confirm_log_id=args.confirm_log_id, port=args.port,
            session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=args.consent_local_read,
            confirm_removals_reviewed=args.confirm_removals_reviewed,
            consent_checkpoint_commit=args.consent_checkpoint_commit)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, TypeError):
        print(json.dumps({"format": FORMAT, "status": "refused"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
