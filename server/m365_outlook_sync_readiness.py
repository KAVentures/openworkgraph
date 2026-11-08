"""Read-only readiness gate combining existing M365 evidence and checkpoint."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
from .m365_outlook_delta import _scope, _grant, _private_read
from .m365_outlook_checkpoint import inspect_checkpoint
from .m365_outlook_owg_receipt import ReceiptError, _unique

FORMAT = "openworkgraph.m365-sync-readiness.v1"


def readiness(*, tenant, mailbox, folder, expected_address, grant_file: Path,
              identity_report: Path, folder_report: Path, ledger_dir: Path,
              log_id, consent_local_read=False, expected_generation=None):
    if consent_local_read is not True:
        raise ReceiptError("Explicit local evidence read consent required")
    scope = _scope(tenant, mailbox, folder)
    _grant(Path(grant_file), tenant, mailbox, folder)
    def load(path):
        try:
            return json.loads(_private_read(Path(path), 8192), object_pairs_hook=_unique)
        except (ValueError, UnicodeError, RecursionError, TypeError):
            raise ReceiptError("Invalid private preflight report") from None
    identity = load(identity_report)
    folder_data = load(folder_report)
    if (type(identity) is not dict
            or identity.get("format") != "openworkgraph.m365-mailbox-identity.v1"
            or identity.get("status") != "mailbox_identity_observed"
            or identity.get("scope_sha256") != scope
            or identity.get("expected_address_sha256") !=
                hashlib.sha256(expected_address.casefold().encode()).hexdigest()
            or identity.get("mailbox_identifier_matched") is not True
            or not (identity.get("upn_matched") is True or identity.get("mail_matched") is True)
            or identity.get("checkpoint_advanced") is not False
            or type(folder_data) is not dict
            or folder_data.get("format") != "openworkgraph.m365-folder-preflight.v1"
            or folder_data.get("status") != "folder_metadata_observed"
            or folder_data.get("scope_sha256") != scope
            or folder_data.get("folder_id_sha256") != hashlib.sha256(folder.encode()).hexdigest()
            or folder_data.get("folder_id_matched") is not True
            or folder_data.get("checkpoint_advanced") is not False):
        raise ReceiptError("Preflight evidence mismatch")
    entry = inspect_checkpoint(tenant=tenant, mailbox=mailbox, folder=folder,
                               grant_file=Path(grant_file), ledger_dir=Path(ledger_dir),
                               consent_local_read=True)
    if entry is None or entry.get("log_id") != log_id:
        raise ReceiptError("No committed checkpoint for requested log")
    # A reviewed operator decision is bound to a specific committed generation.
    # Reject stale review when another operator advances the ledger in between.
    if expected_generation is not None:
        if (type(expected_generation) is not int or expected_generation < 1
                or entry.get("generation") != expected_generation):
            raise ReceiptError("Committed checkpoint generation changed")
    _grant(Path(grant_file), tenant, mailbox, folder)
    return {"format": FORMAT, "status": "operator_review_ready",
            "committed_generation": entry["generation"],
            "mailbox_identity_observed": True, "folder_metadata_observed": True,
            "tenant_identity_verified": False, "provider_requests": 0,
            "checkpoint_advanced": False, "automatic_sync": False}


def extract_after_readiness(*, expected_generation, tenant, mailbox, folder,
                            expected_address, grant_file, identity_report,
                            folder_report, ledger_dir, log_id, confirm_log_id,
                            previous_candidate, previous_csv, receipt_file,
                            journal_dir, handoff_dir, successor_label, output,
                            candidate, token, port=8765, session=None,
                            consent_local_read=False, consent_delta_read=False,
                            consent_local_export=False,
                            confirm_removals_reviewed=False,
                            opener=None, membership_opener=None):
    """Opt-in preflight gate followed by authenticated checkpoint-bound extraction.

    All three independent approvals and an exact committed generation are
    required. This function does not append events or advance checkpoints.
    """
    if (type(expected_generation) is not int or expected_generation < 1
            or consent_local_read is not True
            or consent_delta_read is not True
            or consent_local_export is not True
            or confirm_removals_reviewed is not True
            or log_id != confirm_log_id):
        raise ReceiptError("Explicit reviewed generation and extraction consent required")
    ready = readiness(
        tenant=tenant, mailbox=mailbox, folder=folder,
        expected_address=expected_address, grant_file=grant_file,
        identity_report=identity_report, folder_report=folder_report,
        ledger_dir=ledger_dir, log_id=log_id, consent_local_read=True,
        expected_generation=expected_generation)
    if ready.get("status") != "operator_review_ready":
        raise ReceiptError("Operator preflight not ready")
    # The next-round delegate rechecks the durable predecessor, provider grant,
    # receipt and live OWG membership before contacting Microsoft Graph.
    from .m365_outlook_checkpoint_next import extract_from_checkpoint
    result = extract_from_checkpoint(
        tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id,
        confirm_log_id=confirm_log_id, grant_file=grant_file,
        ledger_dir=ledger_dir, previous_candidate=previous_candidate,
        previous_csv=previous_csv, receipt_file=receipt_file,
        journal_dir=journal_dir, handoff_dir=handoff_dir,
        successor_label=successor_label, output=output, candidate=candidate,
        token=token, port=port, session=session, consent_local_read=True,
        consent_delta_read=True, consent_local_export=True,
        confirm_removals_reviewed=True, opener=opener,
        membership_opener=membership_opener)
    if type(result) is not dict or result.get("status") != "review_required":
        raise ReceiptError("Checkpoint-bound extraction not ready for review")
    # A checkpoint may advance between the first readiness inspection and
    # the delegated Graph read. Never label a result as approved for the
    # reviewed generation when the delegate observed a different predecessor.
    if (type(result.get("committed_predecessor_generation")) is not int
            or result["committed_predecessor_generation"] != expected_generation):
        raise ReceiptError("Checkpoint generation changed during extraction; inspect before retry")
    # A delegate's review candidate is not evidence of a completed import.
    # Fail closed if its response is malformed, claims mutation, or omits
    # counts needed by the operator to review removals and snapshots.
    if (type(result.get("event_snapshots")) is not int
            or result["event_snapshots"] < 0
            or type(result.get("removal_markers_for_review")) is not int
            or result["removal_markers_for_review"] < 0
            or result.get("checkpoint_advanced") is not False
            or result.get("owg_append_performed") is not False):
        raise ReceiptError("Unsafe or incomplete extraction review evidence")
    # Re-read the durable ledger after Graph returns. The delegated receipt
    # identifies the generation it used, but another operator may have
    # committed a newer checkpoint while the provider request was in flight.
    # This is detection, not an atomic lock; never retry automatically.
    current = inspect_checkpoint(
        tenant=tenant, mailbox=mailbox, folder=folder,
        grant_file=Path(grant_file), ledger_dir=Path(ledger_dir),
        consent_local_read=True)
    if (type(current) is not dict or current.get("log_id") != log_id
            or type(current.get("generation")) is not int
            or current["generation"] != expected_generation):
        raise ReceiptError("Checkpoint advanced during Graph read; inspect before retry")
    _grant(Path(grant_file), tenant, mailbox, folder)
    return {"format": FORMAT, "status": "review_required",
            "committed_predecessor_generation": expected_generation,
            "event_snapshots": result["event_snapshots"],
            "removal_markers_for_review": result["removal_markers_for_review"],
            "checkpoint_advanced": False, "owg_append_performed": False,
            "automatic_retry_allowed": False}




def append_after_review(*, expected_generation, tenant, mailbox, folder,
                        expected_address, grant_file, identity_report,
                        folder_report, ledger_dir, log_id, confirm_log_id,
                        candidate, csv_path, port=8765, session=None,
                        consent_local_read=False, consent_local_append=False,
                        confirm_removals_reviewed=False, opener=None):
    """Append a reviewed candidate, verify OWG readback, never advance cursor.

    The caller must independently approve a subsequent checkpoint commit.
    An uncertain POST is deliberately propagated without automatic retry.
    """
    if (type(expected_generation) is not int or expected_generation < 1
            or consent_local_read is not True
            or consent_local_append is not True
            or confirm_removals_reviewed is not True
            or not session or log_id != confirm_log_id):
        raise ReceiptError("Explicit reviewed append approval required")
    readiness(tenant=tenant, mailbox=mailbox, folder=folder,
              expected_address=expected_address, grant_file=grant_file,
              identity_report=identity_report, folder_report=folder_report,
              ledger_dir=ledger_dir, log_id=log_id, consent_local_read=True,
              expected_generation=expected_generation)
    from .m365_outlook_owg_receipt import append_verified
    result = append_verified(
        tenant=tenant, mailbox=mailbox, folder=folder, grant_file=Path(grant_file),
        candidate=Path(candidate), csv_path=Path(csv_path),
        log_id=log_id, confirm_log_id=confirm_log_id, port=port,
        session=session, consent_local_read=True,
        consent_local_append=True, opener=opener)
    # A successful POST is not a checkpoint approval. If the ledger changed
    # concurrently, refuse the handoff; the operator must inspect manually.
    # Once append_verified has returned, OWG may already contain the events.
    # Any subsequent failure must preserve that possibility for the operator;
    # reporting a normal refusal would invite a dangerous blind retry.
    from .m365_outlook_owg_receipt import UncertainReceipt
    try:
        current = inspect_checkpoint(
            tenant=tenant, mailbox=mailbox, folder=folder,
            grant_file=Path(grant_file), ledger_dir=Path(ledger_dir),
            consent_local_read=True)
        if (type(current) is not dict or current.get("log_id") != log_id
                or type(current.get("generation")) is not int
                or current["generation"] != expected_generation):
            raise ReceiptError("Checkpoint changed after append")
        _grant(Path(grant_file), tenant, mailbox, folder)
        if (type(result) is not dict
                or result.get("status") not in ("verified", "verified_empty")
                or result.get("checkpoint_advanced") is not False):
            raise ReceiptError("Append evidence not verified")
    except Exception:
        raise UncertainReceipt(
            "OWG append may have committed; inspect inventory and checkpoint before retry"
        ) from None
    return {"format": FORMAT, "status": "append_review_verified",
            "committed_predecessor_generation": expected_generation,
            "append_receipt": result, "checkpoint_advanced": False,
            "automatic_retry_allowed": False}


def commit_after_review(*, expected_generation, expected_previous_candidate_sha256,
                        tenant, mailbox, folder, expected_address, grant_file,
                        identity_report, folder_report, ledger_dir, log_id,
                        confirm_log_id, candidate, csv_path, receipt_file,
                        journal_dir, handoff_dir, successor_label, port=8765,
                        session=None, consent_local_read=False,
                        confirm_removals_reviewed=False,
                        confirm_events_appended=False,
                        confirm_checkpoint_commit=False,
                        membership_opener=None):
    """Explicit second phase: verify actual OWG membership, then commit cursor.

    Never appends events. The caller must separately append the reviewed
    candidate and explicitly attest to that operation; the checkpoint module
    independently verifies authenticated event membership and the handoff.
    """
    if (type(expected_generation) is not int or expected_generation < 1
            or type(expected_previous_candidate_sha256) is not str
            or len(expected_previous_candidate_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_previous_candidate_sha256)
            or consent_local_read is not True
            or confirm_removals_reviewed is not True
            or confirm_events_appended is not True
            or confirm_checkpoint_commit is not True
            or type(log_id) is not str or not log_id
            or log_id != confirm_log_id or not session):
        raise ReceiptError("Explicit append review and checkpoint approval required")
    readiness(tenant=tenant, mailbox=mailbox, folder=folder,
              expected_address=expected_address, grant_file=grant_file,
              identity_report=identity_report, folder_report=folder_report,
              ledger_dir=ledger_dir, log_id=log_id, consent_local_read=True,
              expected_generation=expected_generation)
    from .m365_outlook_checkpoint import commit_checkpoint
    # Once the writer is invoked, an exception or malformed response cannot
    # prove that no durable generation was committed. Never invite a blind retry.
    from .m365_outlook_owg_receipt import UncertainReceipt
    try:
        result = commit_checkpoint(
            tenant=tenant, mailbox=mailbox, folder=folder, log_id=log_id,
            confirm_log_id=confirm_log_id, grant_file=grant_file,
            candidate=candidate, csv_path=csv_path, receipt_file=receipt_file,
            journal_dir=journal_dir, handoff_dir=handoff_dir,
            successor_label=successor_label, ledger_dir=ledger_dir,
            expected_previous_candidate_sha256=expected_previous_candidate_sha256,
            port=port, session=session, consent_local_read=True,
            confirm_removals_reviewed=True, confirm_checkpoint_commit=True,
            membership_opener=membership_opener)
        if (type(result) is not dict or result.get("status") != "checkpoint_committed"
                or type(result.get("generation")) is not int
                or result["generation"] != expected_generation + 1
                or result.get("idempotent_replay") is not False):
            raise ReceiptError("Unverified checkpoint commit result")
    except Exception:
        raise UncertainReceipt(
            "Checkpoint may have committed; inspect durable ledger before retry"
        ) from None
    return {"format": FORMAT, "status": "checkpoint_committed",
            "committed_generation": result["generation"],
            "event_membership_verified": True,
            "automatic_retry_allowed": False}

def main(argv=None):
    """Local operator CLI: inspect readiness or explicitly request a review candidate.

    Bearer and dashboard credentials are read only from the environment, never
    accepted as command-line arguments or emitted in a response.
    """
    p = argparse.ArgumentParser(description=__doc__)
    for key in ("tenant", "mailbox", "folder", "expected-address", "log-id"):
        p.add_argument("--" + key, required=True)
    for key in ("grant-file", "identity-report", "folder-report", "ledger-dir"):
        p.add_argument("--" + key, required=True, type=Path)
    p.add_argument("--consent-local-read", action="store_true")
    p.add_argument("--expected-generation", type=int)
    p.add_argument("--append-after-review", action="store_true",
                   help="Explicitly append reviewed candidate and verify local OWG readback")
    p.add_argument("--consent-local-append", action="store_true")
    p.add_argument("--commit-after-review", action="store_true",
                   help="Commit only after separately appending reviewed events to OWG")
    p.add_argument("--expected-previous-candidate-sha256")
    p.add_argument("--confirm-events-appended", action="store_true")
    p.add_argument("--confirm-checkpoint-commit", action="store_true")
    p.add_argument("--csv-path", type=Path)
    p.add_argument("--extract-after-readiness", action="store_true",
                   help="Explicitly request a bounded Graph review candidate; never import")
    for key in ("confirm-log-id", "successor-label"):
        p.add_argument("--" + key)
    for key in ("previous-candidate", "previous-csv", "receipt-file",
                "journal-dir", "handoff-dir", "output", "candidate"):
        p.add_argument("--" + key, type=Path)
    p.add_argument("--port", type=int, default=8765)
    for key in ("consent-delta-read", "consent-local-export",
                "confirm-removals-reviewed"):
        p.add_argument("--" + key, action="store_true")
    a = vars(p.parse_args(argv))
    extraction = a.pop("extract_after_readiness")
    committing = a.pop("commit_after_review")
    appending = a.pop("append_after_review")
    try:
        if sum(bool(x) for x in (extraction, committing, appending)) > 1:
            raise ReceiptError("Select exactly one operator action")
        if appending:
            mandatory = ("expected_generation", "confirm_log_id", "candidate", "csv_path")
            if (any(a[k] is None for k in mandatory)
                    or a["expected_generation"] < 1
                    or not all(a[k] is True for k in
                               ("consent_local_read", "consent_local_append",
                                "confirm_removals_reviewed"))
                    or a["log_id"] != a["confirm_log_id"]
                    or any(a[k] is not None for k in
                           ("previous_candidate", "previous_csv", "receipt_file",
                            "journal_dir", "handoff_dir", "successor_label", "output",
                            "expected_previous_candidate_sha256"))
                    or any(a[k] for k in
                           ("consent_delta_read", "consent_local_export",
                            "confirm_events_appended", "confirm_checkpoint_commit"))
                    or not os.environ.get("OWG_DASHBOARD_SESSION")):
                raise ReceiptError("Explicit reviewed append inputs required")
            result = append_after_review(
                **{k: a[k] for k in
                   ("expected_generation", "tenant", "mailbox", "folder",
                    "expected_address", "grant_file", "identity_report",
                    "folder_report", "ledger_dir", "log_id", "confirm_log_id",
                    "candidate", "csv_path", "port", "consent_local_read",
                    "consent_local_append", "confirm_removals_reviewed")},
                session=os.environ["OWG_DASHBOARD_SESSION"])
        elif committing:
            mandatory = ("expected_generation", "confirm_log_id", "candidate",
                         "csv_path", "receipt_file", "journal_dir", "handoff_dir",
                         "successor_label", "expected_previous_candidate_sha256")
            if (any(a[k] is None for k in mandatory)
                    or a["expected_generation"] < 1
                    or not all(a[k] is True for k in
                               ("consent_local_read", "confirm_removals_reviewed",
                                "confirm_events_appended", "confirm_checkpoint_commit"))
                    or a["log_id"] != a["confirm_log_id"]
                    or a["consent_local_append"]
                    or a["consent_delta_read"] or a["consent_local_export"]
                    or a["previous_candidate"] is not None or a["previous_csv"] is not None
                    or a["output"] is not None
                    or not os.environ.get("OWG_DASHBOARD_SESSION")):
                raise ReceiptError("Explicit reviewed checkpoint commit inputs required")
            result = commit_after_review(**{k: a[k] for k in
                ("expected_generation", "expected_previous_candidate_sha256",
                 "tenant", "mailbox", "folder", "expected_address", "grant_file",
                 "identity_report", "folder_report", "ledger_dir", "log_id",
                 "confirm_log_id", "candidate", "csv_path", "receipt_file",
                 "journal_dir", "handoff_dir", "successor_label", "port",
                 "consent_local_read", "confirm_removals_reviewed",
                 "confirm_events_appended", "confirm_checkpoint_commit")},
                 session=os.environ["OWG_DASHBOARD_SESSION"])
        elif extraction:
            # Fail before any Graph or local artifact access when mandatory
            # operator decisions, inputs, or credentials are absent.
            mandatory = ("expected_generation", "confirm_log_id",
                         "previous_candidate", "previous_csv", "receipt_file",
                         "journal_dir", "handoff_dir", "successor_label",
                         "output", "candidate")
            if (any(a[k] is None for k in mandatory)
                    or type(a["expected_generation"]) is not int
                    or a["expected_generation"] < 1
                    or not all(a[k] is True for k in
                               ("consent_local_read", "consent_delta_read",
                                "consent_local_export", "confirm_removals_reviewed"))
                    or a["log_id"] != a["confirm_log_id"]
                    or a["csv_path"] is not None
                    or a["expected_previous_candidate_sha256"] is not None
                    or a["consent_local_append"]
                    or a["confirm_events_appended"] or a["confirm_checkpoint_commit"]
                    or not os.environ.get("OWG_M365_GRAPH_TOKEN")
                    or not os.environ.get("OWG_DASHBOARD_SESSION")):
                raise ReceiptError("Explicit reviewed extraction inputs required")
            result = extract_after_readiness(
                **{k: v for k, v in a.items() if k not in
                   ("csv_path", "expected_previous_candidate_sha256",
                    "confirm_events_appended", "confirm_checkpoint_commit")},
                token=os.environ["OWG_M365_GRAPH_TOKEN"],
                session=os.environ["OWG_DASHBOARD_SESSION"])
        else:
            # Extraction-only arguments must not silently be ignored.
            if (any(a[k] is not None for k in
                    ("confirm_log_id", "successor_label", "csv_path",
                     "expected_previous_candidate_sha256", "previous_candidate",
                     "previous_csv", "receipt_file", "journal_dir", "handoff_dir",
                     "output", "candidate"))
                    or any(a[k] for k in ("confirm_events_appended",
                                          "confirm_checkpoint_commit", "consent_local_append", "consent_delta_read",
                                          "consent_local_export",
                                          "confirm_removals_reviewed"))):
                raise ReceiptError("Extraction options require explicit extraction mode")
            result = readiness(**{k: a[k] for k in
                ("tenant", "mailbox", "folder", "expected_address", "log_id",
                 "grant_file", "identity_report", "folder_report", "ledger_dir",
                 "consent_local_read", "expected_generation")})
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        from .m365_outlook_owg_receipt import UncertainReceipt
        if isinstance(exc, UncertainReceipt):
            print(json.dumps({"format": FORMAT, "status": "unverified",
                              "possible_local_append": True,
                              "automatic_retry_allowed": False}, sort_keys=True))
            return 3
        if not isinstance(exc, (ReceiptError, ValueError, TypeError, KeyError, OSError)):
            raise
        print(json.dumps({"format": FORMAT, "status": "refused",
                          "checkpoint_advanced": False,
                          "automatic_retry_allowed": False}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
