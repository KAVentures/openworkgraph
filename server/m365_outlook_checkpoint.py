"""Explicit, private Microsoft 365 reviewed delta checkpoint ledger.

A checkpoint is an operator-approved local cursor, not proof of complete
provider synchronization. No provider requests or OWG writes are made here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from .m365_outlook_delta import (
    _candidate, _delta_private_parent, _grant, _private_read, _scope,
    MAX_CANDIDATE_BYTES, _valid_link,
)
from .m365_outlook_event_membership import reconcile as reconcile_membership
from .m365_outlook_owg_receipt import ReceiptError, _unique
from .m365_outlook_resume_handoff import prepare_handoff
from .m365_outlook_resume_journal import _canonical

FORMAT = "openworkgraph.m365-checkpoint.v1"
HEX = re.compile(r"[0-9a-f]{64}\Z")


def _entries(directory: Path, scope: str, folder: str):
    """Read a gap-free append-only ledger, refusing ambiguous/corrupt state."""
    prefix = "checkpoint-" + scope + "-"
    entries = []
    for path in directory.iterdir():
        if not path.name.startswith(prefix):
            continue
        suffix = path.name[len(prefix):]
        if not re.fullmatch(r"[0-9]{8}\.json", suffix):
            raise ReceiptError("Unrecognized checkpoint ledger entry")
        try:
            item = json.loads(_private_read(path, 16384), object_pairs_hook=_unique)
        except (ValueError, UnicodeError, TypeError, RecursionError):
            raise ReceiptError("Checkpoint ledger unreadable") from None
        if (type(item) is not dict or set(item) != {
                "format", "scope_sha256", "generation", "candidate_sha256",
                "cursor_url", "previous_entry_sha256", "log_id",
                "event_membership_verified", "removals_reviewed",
                "checkpoint_advanced", "provider_requests"}
                or item["format"] != FORMAT or item["scope_sha256"] != scope
                or type(item["generation"]) is not int
                or item["generation"] != int(suffix[:8])
                or type(item["candidate_sha256"]) is not str
                or not HEX.fullmatch(item["candidate_sha256"])
                or type(item["cursor_url"]) is not str
                or not 1 <= len(item["cursor_url"]) <= 8192
                or type(item["log_id"]) is not str
                or not 1 <= len(item["log_id"]) <= 128
                or item["event_membership_verified"] is not True
                or item["removals_reviewed"] is not True
                or item["checkpoint_advanced"] is not True
                or type(item["provider_requests"]) is not int
                or item["provider_requests"] != 0
                or type(item["event_membership_verified"]) is not bool
                or type(item["removals_reviewed"]) is not bool
                or type(item["checkpoint_advanced"]) is not bool):
            raise ReceiptError("Checkpoint ledger entry invalid")
        # A private ledger is not a trust boundary for provider destinations.
        # Validate the cursor before any future incremental Graph read can use it.
        _valid_link(item["cursor_url"], folder, "delta")
        entries.append(item)
    entries.sort(key=lambda e: e["generation"])
    previous = None
    for index, entry in enumerate(entries, 1):
        if entry["generation"] != index or entry["previous_entry_sha256"] != (
                hashlib.sha256(_canonical(previous) + b"\n").hexdigest()
                if previous is not None else None):
            raise ReceiptError("Checkpoint ledger chain broken")
        previous = entry
    return entries


def inspect_checkpoint(*, tenant, mailbox, folder, ledger_dir: Path,
                       grant_file: Path, consent_local_read=False):
    """Read the latest durable scoped cursor; never return it without live grant."""
    if consent_local_read is not True:
        raise ReceiptError("Explicit local checkpoint read consent required")
    scope = _scope(tenant, mailbox, folder)
    ledger_dir = Path(ledger_dir)
    _delta_private_parent(ledger_dir)
    _grant(Path(grant_file), tenant, mailbox, folder)
    entries = _entries(ledger_dir, scope, folder)
    _grant(Path(grant_file), tenant, mailbox, folder)
    return entries[-1] if entries else None


def commit_checkpoint(*, tenant, mailbox, folder, log_id, confirm_log_id,
                      grant_file: Path, candidate: Path, csv_path: Path,
                      receipt_file: Path, journal_dir: Path, handoff_dir: Path,
                      successor_label: str, ledger_dir: Path,
                      expected_previous_candidate_sha256=None,
                      port=8765, session=None, consent_local_read=False,
                      confirm_removals_reviewed=False,
                      confirm_checkpoint_commit=False,
                      membership_opener=None):
    """Commit only after fresh authenticated membership and reviewed handoff.

    An exclusive generation filename provides same-scope concurrent-writer CAS.
    Any uncertain write requires inspecting the ledger, never blind retry.
    """
    if not all((consent_local_read is True,
                confirm_removals_reviewed is True,
                confirm_checkpoint_commit is True)):
        raise ReceiptError("Separate read, removal review and checkpoint approval required")
    if log_id != confirm_log_id or type(log_id) is not str or not log_id:
        raise ReceiptError("Log confirmation mismatch")
    scope = _scope(tenant, mailbox, folder)
    candidate, csv_path = Path(candidate), Path(csv_path)
    ledger_dir = Path(ledger_dir)
    _delta_private_parent(ledger_dir)
    _delta_private_parent(candidate.parent)
    if any(ledger_dir.resolve() == Path(p).resolve() for p in (
            candidate.parent, Path(journal_dir), Path(handoff_dir))):
        raise ReceiptError("Ledger must use a distinct private directory")
    # The existing parser checks exact scope, private CSV digest and Graph
    # delta cursor shape, including a complete candidate's strict schema.
    cursor = _candidate(candidate, scope, folder, True)
    if csv_path.resolve() != (candidate.parent / json.loads(
            _private_read(candidate, MAX_CANDIDATE_BYTES))["csv_name"]).resolve():
        raise ReceiptError("Candidate CSV mismatch")
    raw = json.loads(_private_read(candidate, MAX_CANDIDATE_BYTES),
                     object_pairs_hook=_unique)
    digest = raw["csv_sha256"]
    decision = prepare_handoff(
        tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id,
        confirm_log_id=confirm_log_id, grant_file=Path(grant_file),
        candidate=candidate, csv_path=csv_path, receipt_file=Path(receipt_file),
        journal_dir=Path(journal_dir), handoff_dir=Path(handoff_dir),
        successor_label=successor_label, port=port, session=session,
        consent_local_read=True, confirm_removals_reviewed=True)
    if decision.get("idempotent_replay") is not True:
        raise ReceiptError("Pre-existing reviewed handoff required")
    membership = reconcile_membership(
        tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id,
        confirm_log_id=confirm_log_id, grant_file=Path(grant_file),
        candidate=candidate, csv_path=csv_path, port=port, session=session,
        consent_local_read=True, opener=membership_opener)
    if membership.get("status") != "all_candidate_events_present":
        raise ReceiptError("Authenticated event membership not established")
    _grant(Path(grant_file), tenant, mailbox, folder)
    entries = _entries(ledger_dir, scope)
    last = entries[-1] if entries else None
    if last and last["candidate_sha256"] == digest:
        if last["log_id"] != log_id or last["cursor_url"] != cursor:
            raise ReceiptError("Conflicting checkpoint replay")
        return {"format": FORMAT, "status": "checkpoint_committed",
                "generation": last["generation"], "idempotent_replay": True}
    if (last is None and expected_previous_candidate_sha256 is not None
            or last is not None and
            expected_previous_candidate_sha256 != last["candidate_sha256"]):
        raise ReceiptError("Stale or missing predecessor checkpoint")
    entry = {
        "format": FORMAT, "scope_sha256": scope,
        "generation": len(entries) + 1, "candidate_sha256": digest,
        "cursor_url": cursor,
        "previous_entry_sha256": (
            hashlib.sha256(_canonical(last) + b"\n").hexdigest()
            if last is not None else None),
        "log_id": log_id, "event_membership_verified": True,
        "removals_reviewed": True, "checkpoint_advanced": True,
        "provider_requests": 0,
    }
    path = ledger_dir / ("checkpoint-" + scope + "-" +
                         f"{entry['generation']:08d}.json")
    payload = _canonical(entry) + b"\n"
    _grant(Path(grant_file), tenant, mailbox, folder)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                     getattr(os, "O_NOFOLLOW", 0), 0o600)
    except FileExistsError:
        raise ReceiptError("Concurrent checkpoint commit; inspect ledger") from None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            directory = os.open(ledger_dir, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError:
        raise ReceiptError("Checkpoint write uncertain; inspect ledger") from None
    return {"format": FORMAT, "status": "checkpoint_committed",
            "generation": entry["generation"], "idempotent_replay": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("inspect", "commit"):
        p = sub.add_parser(action)
        for name in ("tenant", "mailbox", "folder"):
            p.add_argument("--" + name, required=True)
        p.add_argument("--grant-file", required=True, type=Path)
        p.add_argument("--ledger-dir", required=True, type=Path)
        p.add_argument("--consent-local-read", action="store_true")
        if action == "commit":
            for name in ("log-id", "confirm-log-id", "successor-label"):
                p.add_argument("--" + name, required=True)
            for name in ("candidate", "csv-path", "receipt-file",
                         "journal-dir", "handoff-dir"):
                p.add_argument("--" + name, required=True, type=Path)
            p.add_argument("--expected-previous-candidate-sha256")
            p.add_argument("--confirm-removals-reviewed", action="store_true")
            p.add_argument("--confirm-checkpoint-commit", action="store_true")
            p.add_argument("--port", type=int, default=8765)
    args = vars(parser.parse_args(argv))
    action = args.pop("action")
    args["session"] = os.environ.get("OWG_DASHBOARD_SESSION") if action == "commit" else None
    if action == "inspect":
        args.pop("session")
    try:
        result = (inspect_checkpoint(**args) if action == "inspect"
                  else commit_checkpoint(**args))
        if action == "inspect":
            # Do not print provider cursor or private metadata to terminal.
            result = {"format": FORMAT, "status": "checkpoint_present" if result else "empty",
                      "generation": result["generation"] if result else 0}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ReceiptError, ValueError, OSError, TypeError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
