"""Explicitly resume a bounded Microsoft Graph delta extraction from a committed checkpoint.

Read-only Graph extraction; never append, retry an OWG write, or advance the
checkpoint. The checkpoint is an operator decision, not event-level proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from .m365_outlook_checkpoint_commit import FORMAT as CHECKPOINT_FORMAT
from .m365_outlook_delta import (
    DeltaError, _candidate, _delta_private_parent, _grant, _private_read,
    _scope, export_delta,
)
from .m365_outlook_selected import _pairs

FORMAT = "openworkgraph.m365-checkpoint-resume.v1"
CHECKPOINT_KEYS = {
    "format", "status", "scope_sha256", "log_id", "candidate_sha256",
    "csv_sha256", "cursor_url", "removal_markers_reviewed_by_operator",
    "event_level_proof", "provider_requests", "owg_write_requests",
}


def extract_from_checkpoint(*, tenant, mailbox, folder, grant_file: Path,
                            checkpoint_file: Path, previous_candidate: Path,
                            output: Path, candidate: Path, token,
                            consent_read=False, consent_export=False,
                            confirm_checkpoint_resume=False, opener=None):
    if (consent_read is not True or consent_export is not True
            or confirm_checkpoint_resume is not True):
        raise DeltaError("Explicit read, export and checkpoint resume consent required")
    grant_file, checkpoint_file = Path(grant_file), Path(checkpoint_file)
    previous_candidate, output, candidate = map(
        Path, (previous_candidate, output, candidate))
    _delta_private_parent(checkpoint_file.parent)
    if (checkpoint_file.suffix != ".json"
            or not checkpoint_file.name.startswith("checkpoint-")
            or checkpoint_file.parent != previous_candidate.parent
            or checkpoint_file.parent != output.parent
            or checkpoint_file.parent != candidate.parent
            or len({str(p.absolute()) for p in (
                checkpoint_file, previous_candidate, output, candidate, grant_file
            )}) != 5):
        raise DeltaError("Checkpoint resume paths must be distinct and colocated")
    scope = _scope(tenant, mailbox, folder)
    _grant(grant_file, tenant, mailbox, folder)
    try:
        checkpoint = json.loads(_private_read(checkpoint_file, 32768).decode("utf-8"),
                                object_pairs_hook=_pairs)
    except (ValueError, UnicodeError, TypeError, RecursionError):
        raise DeltaError("Invalid committed checkpoint") from None
    if (type(checkpoint) is not dict or set(checkpoint) != CHECKPOINT_KEYS
            or checkpoint["format"] != CHECKPOINT_FORMAT
            or checkpoint["status"] != "operator_checkpoint_committed"
            or checkpoint["scope_sha256"] != scope
            or checkpoint["removal_markers_reviewed_by_operator"] is not True
            or checkpoint["event_level_proof"] is not False
            or checkpoint["provider_requests"] != 0
            or checkpoint["owg_write_requests"] != 0
            or type(checkpoint["log_id"]) is not str
            or not checkpoint["log_id"].startswith("log_")
            or len(checkpoint["log_id"]) != 20):
        raise DeltaError("Checkpoint not committed for this scope")
    digest = hashlib.sha256(_private_read(previous_candidate, 32768)).hexdigest()
    if checkpoint["candidate_sha256"] != digest:
        raise DeltaError("Checkpoint candidate changed")
    cursor = _candidate(previous_candidate, scope, folder, True)
    if checkpoint["cursor_url"] != cursor:
        raise DeltaError("Checkpoint cursor mismatch")
    # _candidate checks the source CSV hash, but also bind it to the checkpoint.
    prior = json.loads(_private_read(previous_candidate, 32768).decode("utf-8"),
                       object_pairs_hook=_pairs)
    if checkpoint["csv_sha256"] != prior["csv_sha256"]:
        raise DeltaError("Checkpoint source CSV mismatch")
    _grant(grant_file, tenant, mailbox, folder)
    result = export_delta(
        tenant=tenant, mailbox=mailbox, folder_id=folder,
        grant_file=grant_file, output=output, candidate=candidate, token=token,
        consent_read=consent_read, consent_export=consent_export,
        previous_candidate=previous_candidate, confirm_previous_import=True,
        opener=opener,
    )
    return {**result, "resume_source": "committed_operator_checkpoint",
            "checkpoint_advanced": False, "automatic_owg_append": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("tenant", "mailbox", "folder"):
        parser.add_argument("--" + name, required=True)
    for name in ("grant-file", "checkpoint-file", "previous-candidate",
                 "output", "candidate"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--consent-delta-read", action="store_true")
    parser.add_argument("--consent-local-export", action="store_true")
    parser.add_argument("--confirm-checkpoint-resume", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = extract_from_checkpoint(
            tenant=args.tenant, mailbox=args.mailbox, folder=args.folder,
            grant_file=args.grant_file, checkpoint_file=args.checkpoint_file,
            previous_candidate=args.previous_candidate, output=args.output,
            candidate=args.candidate, token=os.environ.get("OWG_M365_GRAPH_TOKEN"),
            consent_read=args.consent_delta_read,
            consent_export=args.consent_local_export,
            confirm_checkpoint_resume=args.confirm_checkpoint_resume,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (DeltaError, OSError, ValueError, TypeError, OverflowError, KeyError):
        print(json.dumps({"format": FORMAT, "status": "refused"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
