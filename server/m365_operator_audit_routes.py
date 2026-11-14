"""Local dashboard-only, read-only Microsoft 365 operator audit timeline.

Reads only an operator-configured private directory. Never accepts filesystem
paths, credentials or tenant identifiers from HTTP clients.
"""
from __future__ import annotations

import json
from datetime import datetime
import os
import re
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from .local_auth import dashboard_session_valid

router = APIRouter()
_NAME = re.compile(r"m365-[0-9a-f]{32}\.json\Z")
_ACTIONS = {"readiness", "extract", "append", "handoff", "commit"}
_STATUSES = {"operator_review_ready", "review_ready", "review_required",
             "append_review_verified", "receipt_persisted",
             "next_round_handoff_prepared", "checkpoint_committed"}
_MAX_FILES = 1000
_MAX_BYTES = 2048


class AuditEvidenceError(ValueError):
    pass


def read_operator_audit(directory: Path, *, limit: int = 50) -> dict:
    """Bounded fail-closed inspection; no network or mutation."""
    if type(limit) is not int or not 1 <= limit <= 50:
        raise AuditEvidenceError("Invalid audit limit")
    if os.name != "posix":
        raise AuditEvidenceError("Private audit inspection requires POSIX permissions")
    path = Path(directory)
    try:
        if not path.is_absolute() or not stat.S_ISDIR(path.lstat().st_mode):
            raise AuditEvidenceError("Private audit directory unavailable")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
                raise AuditEvidenceError("Private audit directory permissions invalid")
            names = os.listdir(fd)
            if len(names) > _MAX_FILES:
                raise AuditEvidenceError("Audit directory exceeds bounded inspection")
            selected = sorted((name for name in names if _NAME.fullmatch(name)),
                              reverse=True)
            # Filenames are random, not chronological; inspect all bounded
            # receipts before sorting by the actual recorded UTC timestamp.
            records = []
            for name in selected:
                child = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                dir_fd=fd)
                try:
                    fs = os.fstat(child)
                    if (not stat.S_ISREG(fs.st_mode) or fs.st_nlink != 1
                            or fs.st_mode & 0o077 or fs.st_size > _MAX_BYTES):
                        raise AuditEvidenceError("Unsafe operator audit receipt")
                    with os.fdopen(os.dup(child), "rb") as stream:
                        raw = stream.read(_MAX_BYTES + 1)
                    if len(raw) > _MAX_BYTES:
                        raise AuditEvidenceError("Oversized operator audit receipt")
                    def unique_pairs(pairs):
                        value = {}
                        for key, item in pairs:
                            if key in value:
                                raise AuditEvidenceError("Duplicate audit receipt field")
                            value[key] = item
                        return value
                    obj = json.loads(raw, object_pairs_hook=unique_pairs)
                    if (type(obj) is not dict
                            or obj.get("format") != "openworkgraph.m365-operator-audit.v1"
                            or obj.get("action") not in _ACTIONS
                            or obj.get("status") not in _STATUSES
                            or obj.get("status") not in {
                                "readiness": {"operator_review_ready", "review_ready"},
                                "extract": {"review_required"},
                                "append": {"append_review_verified", "receipt_persisted"},
                                "handoff": {"next_round_handoff_prepared"},
                                "commit": {"checkpoint_committed"},
                            }.get(obj.get("action"), set())
                            or type(obj.get("timestamp_utc")) is not str
                            or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?\+00:00", obj["timestamp_utc"])
                            or type(obj.get("scope_sha256")) is not str
                            or not re.fullmatch(r"[0-9a-f]{64}", obj["scope_sha256"])
                            or type(obj.get("log_id_sha256")) is not str
                            or not re.fullmatch(r"[0-9a-f]{64}", obj["log_id_sha256"])
                            or obj.get("automatic_retry_allowed") is not False):
                        raise AuditEvidenceError("Invalid operator audit receipt")
                    try:
                        stamp = datetime.fromisoformat(obj["timestamp_utc"])
                        if stamp.utcoffset().total_seconds() != 0:
                            raise ValueError("Non-UTC timestamp")
                    except (ValueError, OverflowError, TypeError, AttributeError):
                        raise AuditEvidenceError("Invalid audit timestamp") from None
                    for field in ("expected_generation", "committed_generation",
                                  "committed_predecessor_generation", "event_snapshots",
                                  "removal_markers_for_review"):
                        if field in obj and (type(obj[field]) is not int or obj[field] < 0):
                            raise AuditEvidenceError("Invalid audit count")
                    for field in ("checkpoint_advanced", "owg_append_performed"):
                        if field in obj and type(obj[field]) is not bool:
                            raise AuditEvidenceError("Invalid audit phase flag")
                    if (("checkpoint_advanced" in obj and obj["checkpoint_advanced"] != (obj["action"] == "commit"))
                            or ("owg_append_performed" in obj and obj["owg_append_performed"] != (obj["action"] == "append"))):
                        raise AuditEvidenceError("Contradictory audit phase flags")
                    # Never expose scope hashes or log hashes in browser JSON.
                    item = {key: obj[key] for key in (
                        "timestamp_utc", "action", "status",
                        "expected_generation", "committed_generation",
                        "committed_predecessor_generation", "event_snapshots",
                        "removal_markers_for_review", "checkpoint_advanced",
                        "owg_append_performed") if key in obj}
                    records.append(item)
                finally:
                    os.close(child)
            records.sort(key=lambda item: item["timestamp_utc"], reverse=True)
            return {"format": "openworkgraph.m365-operator-audit-list.v1",
                    "configured": True, "records": records[:limit],
                    "total_receipts": len(records),
                    "truncated": len(records) > limit,
                    "provider_validated": False}
        finally:
            os.close(fd)
    except (OSError, UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise AuditEvidenceError("Private operator audit inspection refused") from None


@router.get("/v1/m365/operator-audit")
def operator_audit(request: Request):
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    configured = os.environ.get("OWG_M365_AUDIT_DIR", "")
    if not configured:
        return JSONResponse({"format": "openworkgraph.m365-operator-audit-list.v1",
                             "configured": False, "records": [],
                             "total_receipts": 0, "truncated": False,
                             "provider_validated": False},
                            headers={"Cache-Control": "no-store"})
    try:
        result = read_operator_audit(Path(configured))
    except AuditEvidenceError:
        raise HTTPException(409, "Private operator audit evidence unavailable") from None
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-checkpoint")
def operator_checkpoint(request: Request, consent_local_read: bool = False):
    """Explicit, read-only checkpoint inspection for the local dashboard.

    Scope and private paths are server-configured, never browser supplied.
    Cursor, tenant, mailbox, folder, grant contents and log ID stay private.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if consent_local_read is not True:
        raise HTTPException(403, "Explicit local checkpoint read consent required")
    names = ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
             "OWG_M365_GRANT_FILE", "OWG_M365_LEDGER_DIR")
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        return JSONResponse({"format": "openworkgraph.m365-dashboard-checkpoint.v1",
                             "configured": False, "checkpoint_present": False,
                             "provider_requests": 0, "checkpoint_advanced": False},
                            headers={"Cache-Control": "no-store"})
    from .m365_outlook_checkpoint import inspect_checkpoint
    try:
        entry = inspect_checkpoint(
            tenant=values[0], mailbox=values[1], folder=values[2],
            grant_file=Path(values[3]), ledger_dir=Path(values[4]),
            consent_local_read=True)
    except (OSError, ValueError, TypeError, KeyError):
        # Revoked/expired grants and corrupt ledger entries are not empty state.
        raise HTTPException(409, "Checkpoint or current grant unavailable; manual review required") from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-checkpoint.v1",
        "configured": True, "checkpoint_present": entry is not None,
        "generation": entry["generation"] if entry else 0,
        "provider_requests": 0, "checkpoint_advanced": False,
        "provider_validated": False, "automatic_retry_allowed": False,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-candidate-review")
def operator_candidate_review(request: Request, consent_local_read: bool = False):
    """Read-only, scoped candidate review; never authorizes append or commit.

    All scope, grant and artifact paths are trusted local operator configuration,
    not HTTP parameters. The review digest is returned for a later *separate*
    explicit approval, never treated as an approval itself.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if consent_local_read is not True:
        raise HTTPException(403, "Explicit candidate review consent required")
    names = ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
             "OWG_M365_GRANT_FILE", "OWG_M365_CANDIDATE_FILE")
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        return JSONResponse({
            "format": "openworkgraph.m365-dashboard-candidate-review.v1",
            "configured": False, "review_ready": False,
            "provider_requests": 0, "owg_writes": 0,
            "checkpoint_advanced": False, "automatic_retry_allowed": False,
        }, headers={"Cache-Control": "no-store"})
    from .m365_outlook_candidate_review import review
    try:
        result = review(
            tenant=values[0], mailbox=values[1], folder=values[2],
            grant_file=Path(values[3]), candidate=Path(values[4]),
            consent_local_read=True)
        if result.get("status") != "review_ready":
            raise ValueError("Candidate not reviewable")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(409, "Candidate or current grant unavailable; manual review required") from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-candidate-review.v1",
        "configured": True, "review_ready": True,
        "event_snapshots": result["event_snapshots"],
        "removal_markers_for_review": result["removal_markers_for_review"],
        "pages": result["pages"],
        "csv_sha256": result["csv_sha256"],
        "separate_append_approval_required": True,
        "provider_requests": 0, "owg_writes": 0,
        "checkpoint_advanced": False, "automatic_retry_allowed": False,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-transaction-preflight")
def operator_transaction_preflight(request: Request, consent_local_read: bool = False,
                                   reviewed_csv_sha256: str = ""):
    """Read-only, exact-digest checkpoint transition proposal; never commits.

    Browser supplies only the digest it actually reviewed. Scope, destination
    log, predecessor digest, grant and private artifacts are local configuration.
    The mutation path must independently revalidate the same conditions.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if consent_local_read is not True:
        raise HTTPException(403, "Explicit transaction preflight consent required")
    if not re.fullmatch(r"[0-9a-f]{64}", reviewed_csv_sha256):
        raise HTTPException(422, "Exact reviewed CSV SHA-256 required")
    names = ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
             "OWG_M365_LOG_ID", "OWG_M365_GRANT_FILE",
             "OWG_M365_CANDIDATE_FILE", "OWG_M365_LEDGER_DIR")
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        return JSONResponse({
            "format": "openworkgraph.m365-dashboard-transaction-preflight.v1",
            "configured": False, "review_ready": False,
            "provider_requests": 0, "owg_writes": 0,
            "checkpoint_advanced": False, "automatic_retry_allowed": False,
        }, headers={"Cache-Control": "no-store"})
    from .m365_outlook_reviewed_transaction import preflight
    from .m365_outlook_owg_receipt import ReceiptError
    previous = os.environ.get("OWG_M365_PREVIOUS_CANDIDATE_SHA256") or None
    try:
        result = preflight(
            tenant=values[0], mailbox=values[1], folder=values[2],
            log_id=values[3], confirm_log_id=values[3],
            grant_file=Path(values[4]), candidate=Path(values[5]),
            ledger_dir=Path(values[6]),
            expected_review_csv_sha256=reviewed_csv_sha256,
            expected_previous_candidate_sha256=previous,
            consent_local_read=True)
        if result.get("status") != "review_ready_for_explicit_commit":
            raise ValueError("Not ready")
    except (ReceiptError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(409, "Preflight refused; inspect candidate, grant and checkpoint manually") from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-transaction-preflight.v1",
        "configured": True, "review_ready": True,
        "proposed_generation": result["proposed_generation"],
        "checkpoint_predecessor_generation": result["checkpoint_predecessor_generation"],
        "reviewed_event_snapshots": result["reviewed_event_snapshots"],
        "removal_markers_for_review": result["removal_markers_for_review"],
        "manual_review_and_separate_commit_required": True,
        "provider_requests": 0, "owg_writes": 0,
        "checkpoint_advanced": False, "automatic_retry_allowed": False,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-reviewed-commit")
def operator_reviewed_commit(
    request: Request, reviewed_csv_sha256: str = "",
    consent_local_read: bool = False, consent_local_append: bool = False,
    confirm_candidate_reviewed: bool = False,
    confirm_removals_reviewed: bool = False,
    confirm_checkpoint_commit: bool = False,
):
    """Single explicit reviewed transaction; never automatically retry a write.

    All filesystem paths, Graph scope, and destination are trusted server
    configuration. The authenticated local dashboard session is passed to the
    existing OWG append adapter, which authenticates independently.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if not all((consent_local_read is True, consent_local_append is True,
                confirm_candidate_reviewed is True,
                confirm_removals_reviewed is True,
                confirm_checkpoint_commit is True)):
        raise HTTPException(403, "Independent read, append, candidate, removal and checkpoint approvals required")
    if not re.fullmatch(r"[0-9a-f]{64}", reviewed_csv_sha256):
        raise HTTPException(422, "Exact reviewed CSV SHA-256 required")
    names = (
        "OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
        "OWG_M365_LOG_ID", "OWG_M365_GRANT_FILE", "OWG_M365_CANDIDATE_FILE",
        "OWG_M365_LEDGER_DIR", "OWG_M365_CSV_PATH", "OWG_M365_RECEIPT_FILE",
        "OWG_M365_JOURNAL_DIR", "OWG_M365_HANDOFF_DIR",
        "OWG_M365_SUCCESSOR_LABEL",
    )
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        raise HTTPException(409, "Microsoft 365 reviewed transaction not configured")
    from .m365_outlook_reviewed_transaction import execute
    from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
    from .m365_outlook_delta import DeltaError
    from .m365_operator_audit_writer import AuditWriteError
    try:
        result = execute(
            tenant=values[0], mailbox=values[1], folder=values[2],
            log_id=values[3], confirm_log_id=values[3],
            grant_file=Path(values[4]), candidate=Path(values[5]),
            ledger_dir=Path(values[6]), csv_path=Path(values[7]),
            receipt_file=Path(values[8]), journal_dir=Path(values[9]),
            handoff_dir=Path(values[10]), successor_label=values[11],
            expected_review_csv_sha256=reviewed_csv_sha256,
            expected_previous_candidate_sha256=(
                os.environ.get("OWG_M365_PREVIOUS_CANDIDATE_SHA256") or None),
            session=authorization[len("owg-session "):].strip(),
            consent_local_read=True, consent_local_append=True,
            confirm_candidate_reviewed=True,
            confirm_removals_reviewed=True, confirm_checkpoint_commit=True,
        )
        if result.get("status") != "checkpoint_committed":
            raise UncertainReceipt("Unconfirmed checkpoint")
    except (ReceiptError, UncertainReceipt, DeltaError, AuditWriteError,
            OSError, ValueError, TypeError, KeyError, RecursionError):
        # The append may already have succeeded. NEVER retry automatically.
        raise HTTPException(
            409, "Outcome may be uncertain; inspect audit, membership and checkpoint manually. Do not retry."
        ) from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-reviewed-commit.v1",
        "status": "checkpoint_committed", "generation": result["generation"],
        "idempotent_replay": result.get("idempotent_replay", False),
        "automatic_retry_allowed": False, "provider_requests": 0,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-next-round-prepare")
def operator_next_round_prepare(
    request: Request, expected_generation: int = 0,
    expected_previous_candidate_sha256: str = "",
    consent_local_read: bool = False, consent_delta_read: bool = False,
    consent_local_export: bool = False,
    confirm_previous_removals_reviewed: bool = False,
):
    """Explicit bounded Graph extraction from a committed predecessor.

    The browser may only supply the generation, predecessor digest and
    independent approvals. All scope, artifacts, destination and credentials
    remain server-controlled. Extraction never appends or commits.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if not all((consent_local_read is True, consent_delta_read is True,
                consent_local_export is True,
                confirm_previous_removals_reviewed is True)):
        raise HTTPException(403, "Independent local, Graph, export and previous-removal approvals required")
    if (type(expected_generation) is not int or expected_generation < 1
            or not re.fullmatch(r"[0-9a-f]{64}", expected_previous_candidate_sha256)):
        raise HTTPException(422, "Exact committed generation and predecessor SHA-256 required")
    names = (
        "OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
        "OWG_M365_LOG_ID", "OWG_M365_GRANT_FILE",
        "OWG_M365_PREVIOUS_CANDIDATE_FILE", "OWG_M365_PREVIOUS_CSV_PATH",
        "OWG_M365_RECEIPT_FILE", "OWG_M365_JOURNAL_DIR",
        "OWG_M365_HANDOFF_DIR", "OWG_M365_LEDGER_DIR",
        "OWG_M365_SUCCESSOR_LABEL", "OWG_M365_CSV_PATH",
        "OWG_M365_CANDIDATE_FILE",
    )
    values = [os.environ.get(name, "") for name in names]
    token = os.environ.get("OWG_M365_GRAPH_TOKEN", "")
    if not all(values) or not token:
        raise HTTPException(409, "Microsoft 365 extraction configuration or credential unavailable")
    from .m365_next_round_review import prepare
    from .m365_outlook_owg_receipt import ReceiptError, UncertainReceipt
    from .m365_outlook_delta import DeltaError
    from .m365_operator_audit_writer import AuditWriteError
    try:
        result = prepare(
            tenant=values[0], mailbox=values[1], folder=values[2],
            log_id=values[3], confirm_log_id=values[3],
            grant_file=Path(values[4]), previous_candidate=Path(values[5]),
            previous_csv=Path(values[6]), receipt_file=Path(values[7]),
            journal_dir=Path(values[8]), handoff_dir=Path(values[9]),
            ledger_dir=Path(values[10]), successor_label=values[11],
            output=Path(values[12]), candidate=Path(values[13]),
            expected_generation=expected_generation,
            expected_previous_candidate_sha256=expected_previous_candidate_sha256,
            token=token, session=authorization[len("owg-session "):].strip(),
            consent_local_read=True, consent_delta_read=True,
            consent_local_export=True, confirm_previous_removals_reviewed=True,
            require_event_membership=True)
        if result.get("status") != "operator_review_required":
            raise ReceiptError("Candidate not ready for review")
    except (ReceiptError, UncertainReceipt, DeltaError, AuditWriteError,
            OSError, ValueError, TypeError, KeyError, RecursionError):
        # A bounded Graph read may have written private candidate artifacts.
        # Do not retry or imply they are safe to commit without new review.
        raise HTTPException(409, "Extraction refused or uncertain; inspect grant, checkpoint and private artifacts manually. Do not retry.") from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-next-round-prepare.v1",
        "status": "operator_review_required",
        "reviewed_csv_sha256": result["reviewed_csv_sha256"],
        "proposed_generation": result["proposed_generation"],
        "event_snapshots": result["event_snapshots"],
        "removal_markers_for_review": result["removal_markers_for_review"],
        "checkpoint_advanced": False, "owg_append_performed": False,
        "automatic_retry_allowed": False, "provider_validated": False,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-recovery-triage")
def operator_recovery_triage(
    request: Request, consent_local_read: bool = False,
    confirm_removals_reviewed: bool = False,
):
    """Authenticated, read-only recovery decision after an uncertain append.

    This never retries an append, contacts Graph or advances a checkpoint.
    The underlying triage authenticates its OWG inventory readback.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if consent_local_read is not True or confirm_removals_reviewed is not True:
        raise HTTPException(403, "Independent local-read and removal-review approvals required")
    names = (
        "OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
        "OWG_M365_LOG_ID", "OWG_M365_GRANT_FILE",
        "OWG_M365_CANDIDATE_FILE", "OWG_M365_CSV_PATH",
        "OWG_M365_RECEIPT_FILE",
    )
    values = [os.environ.get(name, "") for name in names]
    if not all(values):
        raise HTTPException(409, "Microsoft 365 recovery configuration unavailable")
    from .m365_outlook_recovery_triage import triage
    from .m365_outlook_owg_receipt import ReceiptError
    try:
        result = triage(
            tenant=values[0], mailbox=values[1], folder=values[2],
            log_id=values[3], confirm_log_id=values[3],
            grant_file=Path(values[4]), candidate=Path(values[5]),
            csv_path=Path(values[6]), receipt_file=Path(values[7]),
            port=8765, session=authorization[len("owg-session "):].strip(),
            journal_dir=(Path(os.environ["OWG_M365_JOURNAL_DIR"])
                         if os.environ.get("OWG_M365_JOURNAL_DIR") and os.environ.get("OWG_M365_HANDOFF_DIR") else None),
            handoff_dir=(Path(os.environ["OWG_M365_HANDOFF_DIR"])
                         if os.environ.get("OWG_M365_JOURNAL_DIR") and os.environ.get("OWG_M365_HANDOFF_DIR") else None),
            consent_local_read=True, confirm_removals_reviewed=True,
        )
        if result.get("status") not in (
                "receipt_verified", "handoff_verified", "manual_reconciliation_required"):
            raise ReceiptError("Unrecognized recovery result")
    except (ReceiptError, OSError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(409, "Recovery unavailable; inspect local grant, receipt and OWG inventory manually. Do not retry.") from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-recovery-triage.v1",
        "status": result["status"],
        "next_action": {
            "receipt_verified": "operator_may_separately_prepare_handoff",
            "handoff_verified": "operator_may_separately_authorize_next_extraction",
            "manual_reconciliation_required": "inspect_local_evidence_and_authenticated_owg_history",
        }[result["status"]],
        "checkpoint_advanced": False, "owg_writes": 0,
        "provider_requests": 0, "automatic_retry_allowed": False,
    }, headers={"Cache-Control": "no-store"})


@router.post("/v1/m365/operator-identity-folder-preflight")
def operator_identity_folder_preflight(
    request: Request, consent_graph_read: bool = False,
    consent_private_report: bool = False,
):
    """Opt-in, bounded Graph identity and folder checks before any extraction.

    Both checks are independently grant-scoped. Private reports are exclusive
    create-only artifacts; a partial or uncertain outcome must be reconciled
    manually, never retried automatically. Neither check proves tenant identity.
    """
    authorization = str(request.headers.get("authorization") or "")
    if (not authorization.lower().startswith("owg-session ")
            or not dashboard_session_valid(authorization[len("owg-session "):].strip())):
        raise HTTPException(403, "Local dashboard session required")
    if consent_graph_read is not True or consent_private_report is not True:
        raise HTTPException(403, "Separate Graph-read and private-report approvals required")
    names = (
        "OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
        "OWG_M365_EXPECTED_ADDRESS", "OWG_M365_GRANT_FILE",
        "OWG_M365_IDENTITY_REPORT", "OWG_M365_FOLDER_REPORT",
    )
    values = [os.environ.get(name, "") for name in names]
    token = os.environ.get("OWG_M365_GRAPH_TOKEN", "")
    if not all(values) or not token:
        raise HTTPException(409, "Microsoft 365 preflight configuration or credential unavailable")
    from .m365_outlook_mailbox_identity import preflight as identity_preflight
    from .m365_outlook_folder_preflight import preflight as folder_preflight
    try:
        identity = identity_preflight(
            tenant=values[0], mailbox=values[1], folder=values[2],
            expected_address=values[3], grant_file=Path(values[4]),
            output=Path(values[5]), token=token,
            consent_graph_read=True, consent_private_report=True,
        )
        if (identity.get("status") != "mailbox_identity_observed"\n                or identity.get("mailbox_identifier_matched") is not True\n                or identity.get("tenant_id_verified") is not False):
            raise ValueError("Identity preflight refused")
        folder = folder_preflight(
            tenant=values[0], mailbox=values[1], folder=values[2],
            grant_file=Path(values[4]), output=Path(values[6]),
            token=token, consent_graph_read=True, consent_private_report=True,
        )
        if (folder.get("status") != "folder_metadata_observed"\n                or folder.get("folder_id_matched") is not True):
            raise ValueError("Folder preflight refused")
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        raise HTTPException(
            409, "Identity/folder preflight refused or partial; inspect private reports and grant manually. Do not retry."
        ) from None
    return JSONResponse({
        "format": "openworkgraph.m365-dashboard-identity-folder-preflight.v1",
        "status": "identity_and_folder_observed",
        "mailbox_identifier_matched": True,
        "folder_id_matched": True,
        "tenant_verified": False, "sync_authorized": False,
        "owg_writes": 0, "checkpoint_advanced": False,
        "automatic_retry_allowed": False,
    }, headers={"Cache-Control": "no-store"})
