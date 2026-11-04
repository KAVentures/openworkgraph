"""Local backup and restore of everything OpenWorkGraph stores on this computer.

``create_backup`` writes one archive with a consistent copy of every local store:
SQLite databases through SQLite's online backup API, DuckDB databases through
``COPY FROM DATABASE`` while the event-log workspace lock is held, and other
files byte for byte. A manifest records the OWG version and a SHA-256 for every
file, so ``verify_backup`` detects any change or truncation.

Secrets (API tokens, HMAC keys in the auth folder) are left out unless a
passphrase is given. With a passphrase the whole archive is encrypted
(scrypt-derived key, AES-256-GCM), so a copied backup file is unreadable
without it.

Restore is an offline operation (``python -m server.local_backup restore``):
stop OpenWorkGraph first. It verifies the archive, refuses a backup from a newer
OWG version, and moves any existing data folder aside instead of deleting it.
Nothing is uploaded anywhere.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORMAT = "openworkgraph.local-backup.v1"
MANIFEST = "manifest.json"
ENCRYPTED_MAGIC = b"OWGBAK1\n"
_SQLITE_MAGIC = b"SQLite format 3\x00"
_DUCKDB_MAGIC = b"DUCK"
_SKIP_NAMES = {".DS_Store"}
_SKIP_SUFFIXES = ("-wal", "-shm", "-journal", ".wal", ".tmp", ".lock")
_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
MAX_FILES = 20_000


class BackupError(ValueError):
    """Safe error that never echoes file contents."""


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def current_version() -> str:
    try:
        return (_root() / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        return "0"


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for piece in str(value).split("."):
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def data_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_DATA", _root() / "data"))


def auth_dir() -> Path:
    return Path(os.getenv("WORKFLOW_OBSERVER_AUTH_DIR", _root() / "data" / "auth"))


def _kind(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
    except OSError:
        return "file"
    if head.startswith(_SQLITE_MAGIC):
        return "sqlite"
    if head[8:12] == _DUCKDB_MAGIC:
        return "duckdb"
    return "file"


def _skipped(path: Path) -> bool:
    return path.name in _SKIP_NAMES or path.name.endswith(_SKIP_SUFFIXES)


def _copy_sqlite(source: Path, target: Path) -> None:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def _copy_duckdb(source: Path, target: Path) -> None:
    import duckdb

    conn = duckdb.connect(":memory:")
    try:
        conn.execute(f"ATTACH '{_sql_path(source)}' AS src (READ_ONLY)")
        conn.execute(f"ATTACH '{_sql_path(target)}' AS dst")
        conn.execute("COPY FROM DATABASE src TO dst")
    finally:
        conn.close()


def _sql_path(path: Path) -> str:
    return str(path).replace("'", "''")


def _snapshot_file(source: Path, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    kind = _kind(source)
    if kind == "sqlite":
        _copy_sqlite(source, target)
    elif kind == "duckdb":
        try:
            from . import event_log_workspace
            lock = event_log_workspace._LOCK
        except Exception:  # duckdb-less installs still back up everything else
            lock = None
        if lock is not None:
            with lock:
                _copy_duckdb(source, target)
        else:
            _copy_duckdb(source, target)
    else:
        shutil.copy2(source, target)
    return kind


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _collect(base: Path, prefix: str, exclude: Path | None) -> list[tuple[Path, str]]:
    if not base.is_dir():
        return []
    items: list[tuple[Path, str]] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink() or _skipped(path):
            continue
        if exclude is not None and (path == exclude or exclude in path.parents):
            continue
        items.append((path, prefix + path.relative_to(base).as_posix()))
        if len(items) > MAX_FILES:
            raise BackupError(f"more than {MAX_FILES} files; refusing an unbounded backup")
    return items


def _key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, **_SCRYPT).derive(passphrase.encode("utf-8"))


def _encrypt(payload: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, nonce = os.urandom(16), os.urandom(12)
    return ENCRYPTED_MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, payload, ENCRYPTED_MAGIC)


def _decrypt(blob: bytes, passphrase: str | None) -> bytes:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not passphrase:
        raise BackupError("this backup is encrypted; a passphrase is required")
    head = len(ENCRYPTED_MAGIC)
    salt, nonce, body = blob[head:head + 16], blob[head + 16:head + 28], blob[head + 28:]
    try:
        return AESGCM(_key(passphrase, salt)).decrypt(nonce, body, ENCRYPTED_MAGIC)
    except InvalidTag:
        raise BackupError("wrong passphrase or damaged backup") from None


def create_backup(destination: Path, *, passphrase: str | None = None) -> dict[str, Any]:
    """Write a backup archive to ``destination`` and return its manifest."""
    destination = Path(destination)
    if destination.exists():
        raise BackupError("destination already exists; choose a new file name")
    if passphrase is not None and len(passphrase) < 12:
        raise BackupError("use a passphrase of at least 12 characters")
    data, auth = data_dir(), auth_dir()
    files = _collect(data, "data/", exclude=auth if auth.is_relative_to(data) else None)
    if passphrase:
        files += _collect(auth, "auth/", exclude=None)
    if destination.resolve().is_relative_to(data.resolve()):
        raise BackupError("store the backup outside the OpenWorkGraph data folder")
    entries: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="owg-backup-") as scratch:
        staged = Path(scratch)
        for source, name in files:
            target = staged / name
            try:
                kind = _snapshot_file(source, target)
            except FileNotFoundError:
                continue  # removed while backing up; it is not part of this snapshot
            except Exception as exc:
                raise BackupError(f"could not snapshot {name}: {type(exc).__name__}") from None
            entries.append({"path": name, "kind": kind, "bytes": target.stat().st_size,
                            "sha256": _sha256(target)})
        manifest = {
            "format": FORMAT,
            "owg_version": current_version(),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "includes_secrets": bool(passphrase),
            "encrypted": bool(passphrase),
            "file_count": len(entries),
            "total_bytes": sum(entry["bytes"] for entry in entries),
            "files": entries,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST, json.dumps(manifest, indent=2))
            for entry in entries:
                archive.write(staged / entry["path"], entry["path"])
        payload = buffer.getvalue()
    blob = _encrypt(payload, passphrase) if passphrase else payload
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(blob)
    return {key: value for key, value in manifest.items() if key != "files"} | {
        "archive": str(destination), "archive_bytes": len(blob)}


def _open_archive(path: Path, passphrase: str | None) -> zipfile.ZipFile:
    blob = Path(path).read_bytes()
    if blob.startswith(ENCRYPTED_MAGIC):
        blob = _decrypt(blob, passphrase)
    try:
        return zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile:
        raise BackupError("not an OpenWorkGraph backup archive") from None


def verify_backup(path: Path, *, passphrase: str | None = None) -> dict[str, Any]:
    """Check format, file list and every SHA-256; return the manifest summary."""
    with _open_archive(path, passphrase) as archive:
        try:
            manifest = json.loads(archive.read(MANIFEST))
        except (KeyError, ValueError):
            raise BackupError("backup manifest is missing or invalid") from None
        if manifest.get("format") != FORMAT:
            raise BackupError("unsupported backup format")
        listed = {entry["path"] for entry in manifest["files"]}
        present = set(archive.namelist()) - {MANIFEST}
        if listed != present:
            raise BackupError("backup file list does not match its manifest")
        for entry in manifest["files"]:
            name = entry["path"]
            if name.startswith("/") or ".." in Path(name).parts or not name.startswith(("data/", "auth/")):
                raise BackupError("backup contains an unsafe path")
            if hashlib.sha256(archive.read(name)).hexdigest() != entry["sha256"]:
                raise BackupError("a file in the backup was changed or damaged")
    return {key: value for key, value in manifest.items() if key != "files"} | {"verified": True}


def restore_backup(path: Path, *, passphrase: str | None = None, replace: bool = False) -> dict[str, Any]:
    """Offline restore into the configured data/auth folders (stop OWG first)."""
    summary = verify_backup(path, passphrase=passphrase)
    if _version_tuple(summary["owg_version"]) > _version_tuple(current_version()):
        raise BackupError("this backup is from a newer OpenWorkGraph; upgrade before restoring")
    from collector.instance_lock import CollectorLock

    lock = CollectorLock(data_dir())
    if data_dir().is_dir() and not lock.acquire():
        raise BackupError("the recorder is running; quit OpenWorkGraph before restoring")
    lock.release()
    targets = {"data/": data_dir(), "auth/": auth_dir()}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    moved: list[str] = []
    for prefix, target in targets.items():
        if prefix == "auth/" and not summary["includes_secrets"]:
            continue
        if target.exists() and any(target.iterdir()):
            if not replace:
                raise BackupError(f"{target} is not empty; pass replace=True to move it aside first")
            aside = target.with_name(f"{target.name}.pre-restore-{stamp}")
            target.rename(aside)
            moved.append(str(aside))
    auth, data = targets["auth/"], targets["data/"]
    if not summary["includes_secrets"] and moved and auth.is_relative_to(data):
        # Keys are not in this backup: keep this computer's current keys so
        # local tokens and keyed references keep working after the restore.
        previous = Path(moved[0]) / auth.relative_to(data)
        if previous.is_dir():
            shutil.copytree(previous, auth)
    with _open_archive(path, passphrase) as archive:
        manifest = json.loads(archive.read(MANIFEST))
        for entry in manifest["files"]:
            prefix = entry["path"][:5]
            destination = targets[prefix] / entry["path"][5:]
            destination.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(archive.read(entry["path"]))
    return summary | {"restored_files": summary["file_count"], "moved_aside": moved}


def backup_folder() -> Path:
    return Path(os.getenv("OWG_BACKUP_DIR", data_dir().resolve().parent / "owg-backups"))


def list_backups() -> dict[str, Any]:
    folder = backup_folder()
    items = []
    if folder.is_dir():
        for path in sorted(folder.glob("owg-backup-*.zip"), reverse=True)[:50]:
            items.append({"name": path.name, "bytes": path.stat().st_size,
                          "encrypted": path.read_bytes()[:len(ENCRYPTED_MAGIC)] == ENCRYPTED_MAGIC})
    return {"folder": str(folder), "backups": items, "restore": "offline: python -m server.local_backup restore <archive>"}


def create_in_folder(passphrase: str | None = None) -> dict[str, Any]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return create_backup(backup_folder() / f"owg-backup-{stamp}.zip", passphrase=passphrase or None)


def verify_in_folder(name: str, passphrase: str | None = None) -> dict[str, Any]:
    if not isinstance(name, str) or not name.startswith("owg-backup-") or Path(name).name != name:
        raise BackupError("choose a backup from the list")
    path = backup_folder() / name
    if not path.is_file():
        raise BackupError("backup not found")
    return verify_backup(path, passphrase=passphrase or None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Back up or restore local OpenWorkGraph data")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("create", "verify", "restore"):
        command = sub.add_parser(name)
        command.add_argument("archive", type=Path)
        command.add_argument("--passphrase-env", help="environment variable holding the passphrase")
        if name == "restore":
            command.add_argument("--replace", action="store_true",
                                 help="move existing data aside (never deleted) before restoring")
    args = parser.parse_args(argv)
    passphrase = os.environ.get(args.passphrase_env) if args.passphrase_env else None
    if args.passphrase_env and not passphrase:
        parser.error(f"environment variable {args.passphrase_env} is empty")
    try:
        if args.command == "create":
            result = create_backup(args.archive, passphrase=passphrase)
        elif args.command == "verify":
            result = verify_backup(args.archive, passphrase=passphrase)
        else:
            result = restore_backup(args.archive, passphrase=passphrase, replace=args.replace)
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


__all__ = ["BackupError", "create_backup", "create_in_folder", "list_backups", "restore_backup",
           "verify_backup", "verify_in_folder"]

if __name__ == "__main__":
    raise SystemExit(main())
