"""Local-only, verified scheduled snapshots with ownership-verified rotation.

Only a dedicated, opt-in local scheduler job calls this. No secrets, upload,
provider read or extra worker. The ledger records ONLY archives this job created,
so neither manual archives nor lookalike filenames can be pruned.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from . import local_backup

KEEP_SCHEDULED = 14
MAX_TRACKED = 5000
_LEDGER_NAME = ".owg-scheduled-backups-v1.json"
_NAME = re.compile(r"owg-backup-scheduled-[0-9]{8}T[0-9]{18}Z\.zip\Z")


class ScheduledBackupError(RuntimeError):
    """Content-free scheduling failure; never echo paths or work details."""


def _ledger(folder: Path) -> Path:
    return folder / _LEDGER_NAME


def _load(folder: Path) -> list[dict[str, Any]]:
    """Fail closed on a damaged/ambiguous ledger, never discover files by glob."""
    path = _ledger(folder)
    try:
        if not path.exists() and not path.is_symlink():
            return []
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 1 << 20:
            raise ScheduledBackupError("scheduled_backup_ledger_invalid")
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise ScheduledBackupError("scheduled_backup_ledger_invalid") from None
    if (not isinstance(record, dict) or record.get("version") != 1
            or not isinstance(record.get("archives"), list)
            or len(record["archives"]) > MAX_TRACKED):
        raise ScheduledBackupError("scheduled_backup_ledger_invalid")
    entries = record["archives"]
    seen: set[str] = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"name", "bytes", "mtime_ns"}
                or not isinstance(entry["name"], str) or not _NAME.fullmatch(entry["name"])
                or type(entry["bytes"]) is not int or entry["bytes"] <= 0
                or type(entry["mtime_ns"]) is not int or entry["mtime_ns"] <= 0
                or entry["name"] in seen):
            raise ScheduledBackupError("scheduled_backup_ledger_invalid")
        seen.add(entry["name"])
    return entries


def _save(folder: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically commit the owned names before any cleanup can happen."""
    if len(entries) > MAX_TRACKED:
        raise ScheduledBackupError("scheduled_backup_ledger_limit")
    path = _ledger(folder)
    if path.is_symlink():
        raise ScheduledBackupError("scheduled_backup_ledger_invalid")
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".owg-schedule-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": 1, "archives": entries}, stream, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except OSError:
        raise ScheduledBackupError("scheduled_backup_ledger_write_failed") from None


def _owned_file(folder: Path, entry: dict[str, Any]) -> Path | None:
    file = folder / entry["name"]
    try:
        if file.is_symlink() or not file.is_file():
            return None
        current = file.stat()
        if current.st_size != entry["bytes"] or current.st_mtime_ns != entry["mtime_ns"]:
            return None
    except OSError:
        return None
    return file


def _scheduled_archives(folder: Path) -> list[Path]:
    """Existing exact, unchanged archives CREATED by this scheduler, newest first."""
    return [file for entry in sorted(_load(folder), key=lambda x: x["name"], reverse=True)
            if (file := _owned_file(folder, entry)) is not None]


def run() -> dict[str, Any]:
    """Create and verify one unencrypted archive; prune only ledger-owned files.

    Store the new ownership record atomically before any deletion. If a crash
    interrupts pruning, surviving tracked archives are revisited next run;
    an untracked archive is never deleted. Tampered, replaced and symlinked
    files are not deleted even if their names resemble scheduled backups.
    """
    folder = local_backup.backup_folder()
    data = local_backup.data_dir()
    auth = local_backup.auth_dir()
    root = folder.resolve()
    if root.is_relative_to(data.resolve()) or root.is_relative_to(auth.resolve()):
        raise ScheduledBackupError("scheduled_backup_folder_inside_local_data")
    if folder.is_symlink():
        raise ScheduledBackupError("scheduled_backup_folder_symlink_refused")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        entries = _load(folder)  # refuse corrupted ledger before creating a snapshot
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "000000Z"
        path = folder / ("owg-backup-scheduled-" + stamp + ".zip")
        result = local_backup.create_backup(path)
        verified = local_backup.verify_backup(path)
        if not verified["verified"] or verified["encrypted"] or verified["includes_secrets"]:
            raise ScheduledBackupError("scheduled_backup_verification_failed")
        stat = path.stat()
        entries = sorted([
            *entries, {"name": path.name, "bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        ], key=lambda e: e["name"], reverse=True)
        _save(folder, entries)
        # Prune the oldest only if bytes/mtime still match the owned record.
        # Keep all changed or missing entries in the ledger for an operator
        # to investigate; rotation cannot touch files it no longer owns.
        survivors = entries[:KEEP_SCHEDULED]
        for old in entries[KEEP_SCHEDULED:]:
            prior = _owned_file(folder, old)
            if prior is None:
                survivors.append(old)
            else:
                prior.unlink()
        _save(folder, survivors)
        return {
            "verified": True, "encrypted": False, "includes_secrets": False,
            "file_count": result["file_count"],
            "retained_scheduled_backups": len(_scheduled_archives(folder)),
            "local_only": True,
        }
    except (OSError, local_backup.BackupError):
        raise ScheduledBackupError("scheduled_backup_failed") from None


__all__ = ["KEEP_SCHEDULED", "ScheduledBackupError", "run"]
