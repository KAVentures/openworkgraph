"""Scheduled local-only snapshot and rotation for an explicitly opted-in owner.

The existing local scheduler calls this only for the single kind local_backup.
No credentials, passphrases, source reads, uploads or extra background threads.
Only our own recognizable scheduled filenames may be removed by retention.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from . import local_backup

KEEP_SCHEDULED = 14
_NAME = re.compile(r"owg-backup-scheduled-[0-9]{8}T[0-9]{18}Z\.zip\Z")


class ScheduledBackupError(RuntimeError):
    """Content-free scheduling failure; never expose a filesystem/source path."""


def _scheduled_archives(folder: Path) -> list[Path]:
    """Only regular, non-symlink files matching our exact naming scheme."""
    try:
        items = [
            path for path in folder.iterdir()
            if _NAME.fullmatch(path.name) and not path.is_symlink() and path.is_file()
        ]
    except FileNotFoundError:
        return []
    except OSError:
        raise ScheduledBackupError("scheduled_archive_listing_failed") from None
    return sorted(items, key=lambda x: x.name, reverse=True)


def run() -> dict[str, Any]:
    """Create/verify one local archive, then prune old *scheduled* archives.

    Never remove manually created, encrypted or failed-verify archives.
    The newly created backup is an unencrypted local ZIP without credentials.
    Schedule creation requires a separate explicit acknowledgment of this.
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
        # Fractional UTC timestamp makes scheduled filenames disjoint from
        # manual backups, including near-simultaneous manual operations.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "000000Z"
        path = folder / ("owg-backup-scheduled-" + stamp + ".zip")
        result = local_backup.create_backup(path)
        verified = local_backup.verify_backup(path)
        if not verified["verified"] or verified["includes_secrets"] or verified["encrypted"]:
            raise ScheduledBackupError("scheduled_backup_verification_failed")
        older = _scheduled_archives(folder)
        for file in older[KEEP_SCHEDULED:]:
            file.unlink()
        return {
            "verified": True,
            "encrypted": False,
            "includes_secrets": False,
            "file_count": result["file_count"],
            "retained_scheduled_backups": min(KEEP_SCHEDULED, len(older)),
            "local_only": True,
        }
    except (OSError, local_backup.BackupError):
        raise ScheduledBackupError("scheduled_backup_failed") from None


__all__ = ["KEEP_SCHEDULED", "ScheduledBackupError", "run"]
