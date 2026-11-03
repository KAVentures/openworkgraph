"""Explicit local Graph-delta append with durable single-attempt guard and receipt.

No provider calls or checkpoint changes. Never replay a possibly committed POST.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from .m365_outlook_delta import _delta_private_parent, _grant, _scope
from .m365_outlook_owg_receipt import (
    ReceiptError, UncertainReceipt, append_verified, _artifact,
)
from .m365_outlook_resume_journal import _canonical

FORMAT = "openworkgraph.m365-verified-append-persistence.v1"


def _exclusive_private(path: Path, payload: bytes):
    """Durably create, never overwrite. Existing partial records block retries."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            dfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
    except Exception:
        raise UncertainReceipt("Private write uncertain; inspect before retry") from None


def append_and_record(*, tenant, mailbox, folder, grant_file: Path,
                      candidate: Path, csv_path: Path, receipt_file: Path,
                      log_id, confirm_log_id, port, session,
                      consent_local_read=False, consent_local_append=False,
                      opener=None):
    """Create an attempt latch before invoking the production authenticated append."""
    if consent_local_read is not True or consent_local_append is not True:
        raise ReceiptError("Separate local read and append consent required")
    candidate, csv_path = Path(candidate), Path(csv_path)
    receipt_file, grant_file = Path(receipt_file), Path(grant_file)
    _delta_private_parent(receipt_file.parent)
    if (receipt_file.parent != candidate.parent
            or receipt_file in (candidate, csv_path, grant_file)
            or receipt_file.suffix != ".json"
            or not receipt_file.name.startswith("receipt-")
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
                   for c in receipt_file.name)):
        raise ReceiptError("Invalid private receipt path")
    latch = receipt_file.with_name(receipt_file.name + ".attempt")
    if receipt_file.exists() or receipt_file.is_symlink() or latch.exists() or latch.is_symlink():
        raise ReceiptError("Existing receipt or attempt; inspect before retry")
    # Validate immutable candidate and grant before marking the attempt.
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    _, candidate_data = _artifact(candidate, csv_path, scope, folder)
    if not isinstance(log_id, str) or confirm_log_id != log_id:
        raise ReceiptError("Exact target confirmation required")
    attempt = {
        "format": FORMAT, "status": "attempt_started",
        "log_id": log_id, "csv_sha256": candidate_data["csv_sha256"],
        "checkpoint_advanced": False, "automatic_retry": False,
    }
    try:
        _exclusive_private(latch, _canonical(attempt) + b"\n")
    except FileExistsError:
        raise ReceiptError("Attempt already exists; never retry blindly") from None
    # After the latch exists, *any* failure leaves it in place. This is intentional:
    # a POST could have committed, even if the caller saw a network error.
    result = append_verified(
        tenant=tenant, mailbox=mailbox, folder=folder,
        grant_file=grant_file, candidate=candidate, csv_path=csv_path,
        log_id=log_id, confirm_log_id=confirm_log_id, port=port,
        session=session, consent_local_read=True,
        consent_local_append=True, opener=opener,
    )
    if (result.get("status") not in ("verified", "verified_empty")
            or result.get("csv_sha256") != candidate_data["csv_sha256"]
            or result.get("log_id") != log_id
            or result.get("checkpoint_advanced") is not False):
        raise UncertainReceipt("Append receipt invalid; inspect OWG history")
    # Compatible with the existing production resume gate, which reads this JSON.
    try:
        _exclusive_private(receipt_file, _canonical(result) + b"\n")
    except FileExistsError:
        raise UncertainReceipt("Receipt appeared after append; inspect history") from None
    return {"format": FORMAT, "status": "receipt_persisted",
            "log_id": log_id, "events_after": result["events_after"],
            "csv_sha256": result["csv_sha256"],
            "checkpoint_advanced": False, "automatic_retry": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder", "log-id", "confirm-log-id"):
        parser.add_argument("--" + name, required=True)
    for name in ("grant-file", "candidate", "csv-path", "receipt-file"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--consent-local-read", action="store_true")
    parser.add_argument("--consent-local-append", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = append_and_record(
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            grant_file=args.grant_file, candidate=args.candidate,
            csv_path=args.csv_path, receipt_file=args.receipt_file,
            log_id=args.log_id, confirm_log_id=args.confirm_log_id,
            port=args.port, session=os.environ.get("OWG_DASHBOARD_SESSION"),
            consent_local_read=args.consent_local_read,
            consent_local_append=args.consent_local_append)
        print(json.dumps(result, sort_keys=True))
        return 0
    except UncertainReceipt:
        print(json.dumps({"format": FORMAT, "status": "uncertain",
                          "retry_allowed": False}, sort_keys=True))
        return 3
    except (ReceiptError, ValueError, TypeError, OSError):
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "retry_allowed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
