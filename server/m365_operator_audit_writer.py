"""Create-only, minimized receipts for the authenticated local M365 operator dashboard.

The configured directory is private and operator-owned; failure after an OWG
append/checkpoint is an uncertain audit outcome, never permission to retry.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

FORMAT = "openworkgraph.m365-operator-audit.v1"
PHASES = {
    "review": ("readiness", frozenset(("operator_review_ready", "review_ready"))),
    "extract": ("extract", frozenset(("review_required",))),
    "append": ("append", frozenset(("append_review_verified", "receipt_persisted"))),
    "checkpoint-commit": ("commit", frozenset(("checkpoint_committed",))),
}


class AuditWriteError(RuntimeError):
    pass


def preflight_operator_audit(directory: Path) -> None:
    """Refuse an unavailable audit destination before any provider/OWG mutation.

    The writer independently reopens and verifies the directory at write time.
    A preflight cannot eliminate races; post-mutation failures remain uncertain.
    """
    if os.name != "posix":
        raise AuditWriteError("Private audit writer requires POSIX permissions")
    path = Path(directory)
    if not path.is_absolute():
        raise AuditWriteError("Audit directory must be absolute")
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise AuditWriteError("Audit directory is not a real directory")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
                raise AuditWriteError("Audit directory is not private")
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError) as exc:
        raise AuditWriteError("Private audit directory unavailable") from exc


def record_operator_phase(phase: str, result: dict, *, directory: Path,
                          tenant: str, mailbox: str, folder: str,
                          log_id: str | None = None) -> None:
    """Write a private, redacted audit receipt; never store tokens or cursors."""
    if phase not in PHASES:
        return
    # Never manufacture a successful audit status from the phase name.
    if type(result) is not dict or result.get("status") not in PHASES[phase][1]:
        raise AuditWriteError("Operation result is not an auditable success")
    if os.name != "posix":
        raise AuditWriteError("Private audit writer requires POSIX permissions")
    path = Path(directory)
    if not path.is_absolute():
        raise AuditWriteError("Audit directory must be absolute")
    try:
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise AuditWriteError("Audit directory is not a real directory")
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            st = os.fstat(fd)
            if not stat.S_ISDIR(st.st_mode) or st.st_mode & 0o077:
                raise AuditWriteError("Audit directory is not private")
            action, _ = PHASES[phase]
            status = result["status"]
            scope = json.dumps([tenant, mailbox, folder], separators=(",", ":"))
            receipt = {
                "format": FORMAT,
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "action": action, "status": status,
                "scope_sha256": hashlib.sha256(scope.encode()).hexdigest(),
                "log_id_sha256": hashlib.sha256((log_id or "").encode()).hexdigest(),
                "automatic_retry_allowed": False,
            }
            for field in ("expected_generation", "committed_generation",
                          "committed_predecessor_generation", "event_snapshots",
                          "removal_markers_for_review"):
                if field in result and type(result[field]) is int and result[field] >= 0:
                    receipt[field] = result[field]
            if phase == "checkpoint-commit" and type(result.get("generation")) is int:
                receipt["committed_generation"] = result["generation"]
            receipt["checkpoint_advanced"] = phase == "checkpoint-commit"
            receipt["owg_append_performed"] = phase == "append"
            data = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
            if len(data) > 2048:
                raise AuditWriteError("Audit receipt too large")
            name = "m365-" + secrets.token_hex(16) + ".json"
            out = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o600, dir_fd=fd)
            try:
                with os.fdopen(out, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.fsync(fd)
            except OSError as exc:
                raise AuditWriteError("Audit write uncertain; inspect receipt directory") from exc
        finally:
            os.close(fd)
    except (OSError, ValueError, TypeError) as exc:
        raise AuditWriteError("Private audit write refused or uncertain") from exc
