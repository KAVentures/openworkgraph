"""Bounded read-only health check for local OWG backup archives (R20/M7).

Unlike the backup list, which only reads names and encryption markers, this
explicit operation verifies full plaintext ZIP contents and their SHA-256
manifest hashes. Only already-listed basenames are accepted. No restore,
passphrases, cloud upload or arbitrary file reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import stat
from typing import Any

from . import local_backup

MAX_SELECTED = 5
MAX_ARCHIVE_BYTES = 1 << 30  # 1 GiB per archive for a dashboard check
MAX_TOTAL_ARCHIVE_BYTES = 2 << 30  # 2 GiB of ZIP input per request
_NAME = re.compile(r"owg-backup-[A-Za-z0-9_.-]{1,128}\.zip\Z")


class BackupHealthError(ValueError):
    """Safe, content-free owner-facing error."""


def _validate(spec: Any) -> list[str]:
    if type(spec) is not dict or set(spec) != {"names", "confirm_local_verification"}:
        raise BackupHealthError("choose a listed local backup and confirm verification")
    if spec["confirm_local_verification"] is not True:
        raise BackupHealthError("explicit confirmation required to inspect local backups")
    names = spec["names"]
    if (type(names) is not list or not 1 <= len(names) <= MAX_SELECTED
            or any(type(name) is not str or not _NAME.fullmatch(name) for name in names)
            or len(set(names)) != len(names)):
        raise BackupHealthError("choose one to five distinct listed local backup names")
    return names


def _fingerprint(path: Path):
    """A no-follow file signature; never follow a symlink supplied via backups."""
    if path.is_symlink():
        return None
    try:
        st = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)


def check(spec: Any) -> dict[str, Any]:
    names = _validate(spec)
    # Reuse the existing local backup catalog. It intentionally caps its
    # listed population; an omitted file must not be probed by name.
    listing = local_backup.list_backups()
    listed = {item["name"]: item for item in listing["backups"]}
    if any(name not in listed for name in names):
        raise BackupHealthError("backup selection is no longer in the local list")
    folder = local_backup.backup_folder()
    results: list[dict[str, Any]] = []
    inspected_bytes = 0
    stamp = datetime.now(timezone.utc).isoformat()
    for name in names:
        item = listed[name]
        row: dict[str, Any] = {
            "name": name, "archive_bytes": int(item["bytes"]),
            "status": "not_verified", "encrypted": bool(item["encrypted"]),
            "verified": False, "owg_version": None, "file_count": None,
        }
        path = folder / name
        before = _fingerprint(path)
        if before is None:
            row["status"] = "unavailable_or_not_regular"
        elif row["encrypted"]:
            # Legacy encrypted backups need the passphrase-aware OFFLINE CLI.
            # A dashboard must never request or transport the passphrase.
            row["status"] = "encrypted_requires_offline_cli"
        elif before[2] > MAX_ARCHIVE_BYTES:
            row["status"] = "exceeds_dashboard_archive_limit"
        elif inspected_bytes + before[2] > MAX_TOTAL_ARCHIVE_BYTES:
            row["status"] = "exceeds_dashboard_request_budget"
        else:
            inspected_bytes += before[2]
            try:
                verified = local_backup.verify_backup(path)
            except local_backup.BackupError:
                row["status"] = "integrity_check_failed"
            except OSError:
                row["status"] = "unavailable_or_not_regular"
            else:
                if verified.get("includes_secrets") or verified.get("encrypted"):
                    row["status"] = "requires_offline_inspection"
                else:
                    row.update(status="verified", verified=True,
                               owg_version=verified.get("owg_version"),
                               file_count=verified.get("file_count"))
            if _fingerprint(path) != before:
                # It may have been swapped while read. Do not claim the
                # archive we just checked is still the listed file.
                row.update(status="changed_during_check", verified=False,
                           owg_version=None, file_count=None)
        results.append(row)
    return {
        "format": "openworkgraph.local-backup-health.v1",
        "checked_at": stamp, "items": results,
        "requested": len(names),
        "verified": sum(bool(item["verified"]) for item in results),
        "not_verified": sum(not item["verified"] for item in results),
        "archive_bytes_read_budgeted": inspected_bytes,
        "max_archives_per_check": MAX_SELECTED,
        "max_single_archive_bytes": MAX_ARCHIVE_BYTES,
        "max_total_archive_bytes": MAX_TOTAL_ARCHIVE_BYTES,
        "all_selected_verified": all(item["verified"] for item in results),
        "restores_performed": 0, "archive_contents_returned": False,
        "offline_restore_authorized": False, "local_only": True,
        "interpretation": ("Verified means that this exact saved plaintext archive passed a "
                           "read-only integrity check at checked_at. It does not prove that "
                           "restoring it will succeed on the current computer or that "
                           "the original source data was complete. Encrypted/oversize "
                           "archives require independent offline CLI checks."),
    }


__all__ = ["BackupHealthError", "MAX_SELECTED", "check"]
