"""Local, explicitly saved reconstruction labels; never confirmed case facts.

Only a single short-lived review snapshot is retained. Saved labels are not
shared with agents, and a changed evidence digest or history grant invalidates
the snapshot. Storage is private, atomic and bounded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
from typing import Any

from .local_auth import auth_dir
from shared.history_policy import read_policy

_LOCK = threading.RLock()
_FILENAME = ".reconstruction_review_labels.json"
_MAX_BYTES = 131072
_TTL = timedelta(hours=1)


def _path() -> Path:
    return auth_dir() / _FILENAME


def _lease_key(grant: dict[str, Any], generation: int) -> str:
    return hashlib.sha256(json.dumps(
        {"grant": grant, "generation": generation}, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()


@contextmanager
def _store_guard():
    """Serialize snapshot operations across processes using a persistent lock inode."""
    with _LOCK:
        lock_path = _path().with_name(_FILENAME + ".lock")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(lock_path, flags, 0o600)
        try:
            info = os.fstat(fd)
            # Windows lacks O_NOFOLLOW: reject an existing symlink or a
            # path swapped after open, rather than locking an unrelated file.
            path_info = lock_path.lstat()
            if (not stat.S_ISREG(info.st_mode)
                    or not stat.S_ISREG(path_info.st_mode)
                    or (hasattr(info, "st_ino") and info.st_ino != path_info.st_ino)
                    or (os.name == "posix" and info.st_mode & 0o077)):
                raise OSError("unsafe review lock")
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                if info.st_size == 0:
                    os.write(fd, bytes([0]))
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def clear_saved_reconstruction_review() -> None:
    with _store_guard():
        _path().unlink(missing_ok=True)


def _retention_allowed() -> bool:
    retention = read_policy().get("human_retention", {})
    return isinstance(retention, dict) and retention.get("mode") in ("days", "forever")


def _load(grant: dict[str, Any], generation: int, evidence_sha256: str) -> dict[str, Any] | None:
    path = _path()
    try:
        # Read through a bounded file descriptor rather than checking a path
        # and reopening it: a replacement between lstat and read_text could
        # otherwise redirect the read to a symlink or an oversized file.
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES
                    or (os.name == "posix" and info.st_mode & 0o077)):
                return None
            # On platforms without O_NOFOLLOW, compare the opened inode
            # against the path and reject links rather than following them.
            path_info = path.lstat()
            if (not stat.S_ISREG(path_info.st_mode)
                    or (hasattr(info, "st_ino") and info.st_ino != path_info.st_ino)):
                return None
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                return None
            value = json.loads(raw.decode("utf-8"))
        finally:
            os.close(fd)
    except (FileNotFoundError, OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    try:
        expiry = datetime.fromisoformat(value["expires_at"])
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if (expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)
            or expiry > datetime.now(timezone.utc) + _TTL
            or value.get("lease_key") != _lease_key(grant, generation)
            or value.get("evidence_sha256") != evidence_sha256
            or not isinstance(value.get("annotations"), list)):
        return None
    return value


def read_saved_reconstruction_review(
    grant: dict[str, Any], generation: int, evidence_sha256: str,
) -> dict[str, Any] | None:
    with _store_guard():
        if not _retention_allowed() or grant.get("mode") != "all_saved":
            _path().unlink(missing_ok=True)
            return None
        result = _load(grant, generation, evidence_sha256)
        # Loading and decoding evidence may overlap a consent/retention change.
        # Revalidate before returning labels to the authenticated caller.
        if (result is None or grant.get("mode") != "all_saved"
                or not _retention_allowed()):
            _path().unlink(missing_ok=True)
            return None
        return {
            "evidence_sha256": result["evidence_sha256"],
            "annotations": result["annotations"],
            "expires_at": result["expires_at"],
            "reviewer_declared_not_independently_verified": True,
            "confirmed_cases": False,
        }


def save_reconstruction_review(
    grant: dict[str, Any], generation: int, envelope: dict[str, Any],
) -> dict[str, Any]:
    if grant.get("mode") != "all_saved" or not _retention_allowed():
        raise PermissionError("Saved review requires retained history and explicit all-saved grant")
    if not isinstance(envelope, dict):
        raise ValueError("review envelope must be an object")
    digest = envelope.get("evidence_sha256")
    annotations = envelope.get("annotations")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not isinstance(annotations, list) or not 1 <= len(annotations) <= 192):
        raise ValueError("invalid bounded review")
    expires_at = (datetime.now(timezone.utc) + _TTL).isoformat()
    record = {
        "version": 1, "lease_key": _lease_key(grant, generation),
        "evidence_sha256": digest, "annotations": annotations,
        "expires_at": expires_at,
    }
    try:
        payload = json.dumps(record, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid review annotation payload") from exc
    if len(payload) > _MAX_BYTES:
        raise ValueError("review exceeds private storage limit")
    with _store_guard():
        # Retention can change while the payload is serialized. Recheck at
        # the atomic persistence boundary under the same store lock.
        if grant.get("mode") != "all_saved" or not _retention_allowed():
            raise PermissionError("Review retention was revoked before persistence")
        path = _path()
        if path.is_symlink():
            raise OSError("unsafe review storage path")
        fd, temp = tempfile.mkstemp(prefix=".reconstruction-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            if os.name == "posix":
                os.chmod(temp, 0o600)
            os.replace(temp, path)
            # Consent may change while the atomic replace is executing.
            # Never acknowledge retention when it was withdrawn mid-write.
            if grant.get("mode") != "all_saved" or not _retention_allowed():
                path.unlink(missing_ok=True)
                raise PermissionError("Review consent or retention was revoked during persistence")
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
    return {"status": "saved_locally", "expires_at": expires_at,
            "confirmed_cases": False, "agent_access_granted": False}
