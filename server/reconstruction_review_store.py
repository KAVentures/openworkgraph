"""Local, explicitly saved reconstruction labels; never confirmed case facts.

Only a single short-lived review snapshot is retained. Saved labels are not
shared with agents, and a changed evidence digest or history grant invalidates
the snapshot. Storage is private, atomic and bounded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
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


def clear_saved_reconstruction_review() -> None:
    with _LOCK:
        _path().unlink(missing_ok=True)


def _retention_allowed() -> bool:
    retention = read_policy().get("human_retention", {})
    return isinstance(retention, dict) and retention.get("mode") in ("days", "forever")


def _load(grant: dict[str, Any], generation: int, evidence_sha256: str) -> dict[str, Any] | None:
    path = _path()
    try:
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink()
                or info.st_size > _MAX_BYTES or
                (os.name == "posix" and info.st_mode & 0o077)):
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError, UnicodeError):
        return None
    if not isinstance(value, dict) or value.get("version") != 1:
        return None
    try:
        expiry = datetime.fromisoformat(value["expires_at"])
    except (KeyError, ValueError, TypeError):
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
    with _LOCK:
        if not _retention_allowed() or grant.get("mode") != "all_saved":
            clear_saved_reconstruction_review()
            return None
        result = _load(grant, generation, evidence_sha256)
        if result is None:
            clear_saved_reconstruction_review()
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
    payload = json.dumps(record, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > _MAX_BYTES:
        raise ValueError("review exceeds private storage limit")
    with _LOCK:
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
        finally:
            try:
                os.unlink(temp)
            except FileNotFoundError:
                pass
    return {"status": "saved_locally", "expires_at": expires_at,
            "confirmed_cases": False, "agent_access_granted": False}
