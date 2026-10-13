"""Persistent *local-only* key for Gateway upload pseudonyms.

Unlike a device bearer token this key is never an HTTP credential. It is
created once per local installation and survives token re-enrollment/rotation.
Refuse to silently regenerate a corrupt/unreadable key, as that would break
pseudonym continuity and conceal local privacy-state failures.
"""
from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

_KEY_NAME = ".gateway_upload_privacy_key"
_BYTES = 32


def _read(path: Path) -> str:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or (os.name != "nt" and info.st_mode & 0o077):
        raise PermissionError("unsafe local Gateway privacy key file")
    value = path.read_text(encoding="ascii").strip()
    if len(value) != _BYTES * 2:
        raise ValueError("invalid local Gateway privacy key")
    try:
        raw = bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError("invalid local Gateway privacy key") from exc
    if len(raw) != _BYTES:
        raise ValueError("invalid local Gateway privacy key")
    return value


def load_or_create_upload_privacy_key(auth_dir: Path) -> str:
    """Generate a 256-bit random local key atomically, never send it to Gateway.

    Filesystem failures are fatal for sharing. Independent device-token
    rotation must not change this key. The configured auth directory must
    be a private device-local directory, not a synced/shared cloud folder.
    """
    directory = Path(auth_dir)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / _KEY_NAME
    try:
        return _read(path)
    except FileNotFoundError:
        pass
    # O_EXCL means simultaneous sync workers cannot both replace the key.
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _read(path)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(secrets.token_hex(_BYTES) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        # Never upload when the key could not be persisted.
        raise
    return _read(path)
