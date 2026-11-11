"""Local dashboard-only, read-only Microsoft 365 operator audit timeline.

Reads only an operator-configured private directory. Never accepts filesystem
paths, credentials or tenant identifiers from HTTP clients.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from .local_auth import dashboard_session_valid

router = APIRouter()
_NAME = re.compile(r"m365-[0-9a-f]{32}\.json\Z")
_ACTIONS = {"readiness", "extract", "append", "commit"}
_STATUSES = {"operator_review_ready", "review_ready", "review_required",
             "append_review_verified", "receipt_persisted", "checkpoint_committed"}
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
                    obj = json.loads(raw)
                    if (type(obj) is not dict
                            or obj.get("format") != "openworkgraph.m365-operator-audit.v1"
                            or obj.get("action") not in _ACTIONS
                            or obj.get("status") not in _STATUSES
                            or obj.get("status") not in {
                                "readiness": {"operator_review_ready", "review_ready"},
                                "extract": {"review_required"},
                                "append": {"append_review_verified", "receipt_persisted"},
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
