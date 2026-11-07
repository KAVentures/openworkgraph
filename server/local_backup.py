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
import re
import shutil
import stat
import zlib
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
MAX_MANIFEST_BYTES = 16 << 20  # enough for 20,000 entries; never read an unbounded manifest
MAX_ENTRY_BYTES = 50 << 30
MAX_TOTAL_BYTES = 100 << 30
MAX_ENCRYPTED_ARCHIVE_BYTES = 2 << 30  # legacy AES-GCM format decrypts in memory
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")


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
    """Read an ordinary archive from disk; legacy encrypted archives need a bounded in-memory decrypt."""
    path = Path(path)
    try:
        with path.open("rb") as handle:
            encrypted = handle.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC
        if encrypted:
            if path.stat().st_size > MAX_ENCRYPTED_ARCHIVE_BYTES:
                raise BackupError("encrypted backup exceeds the supported in-memory verification limit")
            blob = _decrypt(path.read_bytes(), passphrase)
            source: Path | io.BytesIO = io.BytesIO(blob)
        else:
            source = path
        return zipfile.ZipFile(source)
    except (zipfile.BadZipFile, OSError):
        raise BackupError("backup archive cannot be read") from None


def _safe_member(name: str) -> bool:
    """Manifest paths are POSIX, including on Windows; no ambiguous separators or aliases."""
    if not isinstance(name, str) or not name or "\\" in name or "\x00" in name or ":" in name:
        return False
    parts = name.split("/")
    return (len(parts) >= 2 and parts[0] in ("data", "auth")
            and all(part not in ("", ".", "..") for part in parts)
            and not name.startswith("/") and name != MANIFEST)


def _regular_member(info: zipfile.ZipInfo) -> bool:
    # Ignore DOS/Unix mode when absent, but never extract symlinks, dirs, devices or FIFOs.
    mode = info.external_attr >> 16
    return not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG)


def _validated_manifest(archive: zipfile.ZipFile, *, encrypted: bool) -> dict[str, Any]:
    """Validate the complete archive directory and manifest BEFORE reading data members."""
    infos = archive.infolist()
    if len(infos) > MAX_FILES + 1 or len({x.filename for x in infos}) != len(infos):
        raise BackupError("backup contains too many or duplicate entries")
    index = {x.filename: x for x in infos}
    metadata = index.get(MANIFEST)
    if metadata is None or not _regular_member(metadata) or metadata.file_size > MAX_MANIFEST_BYTES:
        raise BackupError("backup manifest is missing or invalid")
    try:
        with archive.open(metadata) as handle:
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise ValueError()
        manifest = json.loads(raw)
    except (ValueError, UnicodeError, EOFError, RuntimeError, zipfile.BadZipFile, zlib.error):
        raise BackupError("backup manifest is missing or invalid") from None
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise BackupError("unsupported backup format")
    entries = manifest.get("files")
    if not isinstance(entries, list) or len(entries) > MAX_FILES:
        raise BackupError("backup file list is invalid or too large")
    if not isinstance(manifest.get("owg_version"), str) or not 0 < len(manifest["owg_version"]) <= 80:
        raise BackupError("backup version is missing or invalid")
    if type(manifest.get("includes_secrets")) is not bool or type(manifest.get("encrypted")) is not bool:
        raise BackupError("backup encryption metadata is invalid")
    if manifest["encrypted"] != encrypted or (manifest["includes_secrets"] and not encrypted):
        raise BackupError("backup encryption metadata does not match the archive")
    if type(manifest.get("file_count")) is not int or manifest["file_count"] != len(entries):
        raise BackupError("backup file count does not match its manifest")
    total = 0
    names: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError("backup file entry is invalid")
        name, kind, size, digest = (entry.get("path"), entry.get("kind"),
                                   entry.get("bytes"), entry.get("sha256"))
        if not _safe_member(name) or name in names:
            raise BackupError("backup contains an unsafe or duplicate path")
        if name.startswith("auth/") and not manifest["includes_secrets"]:
            raise BackupError("unencrypted backup cannot contain credentials")
        if kind not in ("sqlite", "duckdb", "file") or type(size) is not int or size < 0 or size > MAX_ENTRY_BYTES:
            raise BackupError("backup contains invalid file metadata or an oversized entry")
        if not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
            raise BackupError("backup contains an invalid file digest")
        info = index.get(name)
        if info is None or not _regular_member(info) or info.file_size != size:
            raise BackupError("backup file list or sizes do not match the manifest")
        names.add(name)
        total += size
        if total > MAX_TOTAL_BYTES:
            raise BackupError("backup exceeds the supported restored-data limit")
    if names != set(index) - {MANIFEST}:
        raise BackupError("backup file list does not match its manifest")
    if type(manifest.get("total_bytes")) is not int or manifest["total_bytes"] != total:
        raise BackupError("backup total size does not match its manifest")
    return manifest


def verify_backup(path: Path, *, passphrase: str | None = None) -> dict[str, Any]:
    """Stream every member, validating hashes, counts, archive structure and path safety."""
    path = Path(path)
    try:
        with path.open("rb") as head:
            encrypted = head.read(len(ENCRYPTED_MAGIC)) == ENCRYPTED_MAGIC
        with _open_archive(path, passphrase) as archive:
            manifest = _validated_manifest(archive, encrypted=encrypted)
            for entry in manifest["files"]:
                digest = hashlib.sha256()
                size = 0
                with archive.open(entry["path"]) as handle:
                    for chunk in iter(lambda: handle.read(1 << 20), b""):
                        digest.update(chunk)
                        size += len(chunk)
                        if size > entry["bytes"]:
                            raise BackupError("backup entry exceeds its declared size")
                if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                    raise BackupError("a file in the backup was changed or damaged")
    except (zipfile.BadZipFile, EOFError, RuntimeError, zlib.error):
        raise BackupError("a file in the backup was changed or damaged") from None
    return {key: value for key, value in manifest.items() if key != "files"} | {"verified": True}


def _recorder_running(directory: Path) -> bool:
    """Read-only advisory-lock probe. A missing lock file means no collector owns it."""
    lock_path = directory / ".collector.lock"
    if not lock_path.exists():
        return False
    try:
        with lock_path.open("rb") as handle:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    return True
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
    except OSError:
        raise BackupError("could not inspect recorder lock; stop OpenWorkGraph and retry") from None
    return False


def plan_restore(path: Path, *, passphrase: str | None = None, replace: bool = False) -> dict[str, Any]:
    """Read-only offline recovery dry-run; never moves, creates or extracts local data."""
    verified = verify_backup(path, passphrase=passphrase)
    data, auth = data_dir(), auth_dir()
    blockers: list[str] = []
    if _version_tuple(verified["owg_version"]) > _version_tuple(current_version()):
        blockers.append("backup was created by a newer OpenWorkGraph version")
    if data == auth or data.is_relative_to(auth):
        blockers.append("data/auth folders overlap in an unsupported configuration")
    if any(p.is_symlink() or (p.exists() and not p.is_dir()) for p in (data, auth)):
        blockers.append("data or credential target is not a normal directory")
    elif _recorder_running(data):
        blockers.append("recorder is running; quit OpenWorkGraph before restoring")
    nested = auth.is_relative_to(data)
    targets = [data] + ([auth] if verified["includes_secrets"] and not nested else [])
    occupied = [p for p in targets if p.is_dir() and any(c.name != ".collector.lock" for c in p.iterdir())]
    if occupied and not replace:
        blockers.append("existing local data would be replaced; explicitly select --replace")
    return {
        **verified, "ready": not blockers, "blockers": blockers,
        "replace_requested": bool(replace), "requires_replace": bool(occupied),
        "data_target": str(data), "auth_target": str(auth),
        "auth_inside_data": nested,
        "operation": "offline_restore_preflight_only", "writes_performed": False,
    }


def restore_backup(path: Path, *, passphrase: str | None = None, replace: bool = False) -> dict[str, Any]:
    """Stage verified data first, then swap offline stores, rolling back failed swaps.

    All archive files are extracted and rehashed in a same-volume staging directory
    before an existing store is renamed. The recorder must be stopped. A completed
    restore keeps pre-restore folders instead of deleting them.
    """
    summary = plan_restore(path, passphrase=passphrase, replace=replace)
    if not summary["ready"]:
        raise BackupError("; ".join(summary["blockers"]))
    from collector.instance_lock import CollectorLock

    data, auth = data_dir(), auth_dir()
    nested = auth.is_relative_to(data)
    if (not nested and auth.is_relative_to(data)) or data == auth:
        raise BackupError("unsupported data and credential target layout")
    data.parent.mkdir(parents=True, exist_ok=True)
    # Only acquire when a store exists; creating a lock in an absent directory
    # would turn an otherwise empty restore target into a conflicting target.
    lock = CollectorLock(data) if data.is_dir() else None
    if lock is not None and not lock.acquire():
        raise BackupError("the recorder is running; quit OpenWorkGraph before restoring")
    try:
        with tempfile.TemporaryDirectory(prefix=".owg-restore-stage-", dir=data.parent) as scratch:
            root = Path(scratch)
            staged_data = root / "data"
            staged_auth = root / "auth"
            staged_data.mkdir()
            # An archive without secrets deliberately keeps the local auth store.
            if nested and not summary["includes_secrets"] and auth.is_dir():
                shutil.copytree(auth, staged_data / auth.relative_to(data), symlinks=True)
            try:
                with _open_archive(path, passphrase) as archive:
                    validated = _validated_manifest(archive, encrypted=summary["encrypted"])
                    if (validated["file_count"] != summary["file_count"]
                            or validated["total_bytes"] != summary["total_bytes"]
                            or validated["owg_version"] != summary["owg_version"]):
                        raise BackupError("backup changed after preflight")
                    for entry in validated["files"]:
                        name = entry["path"]
                        prefix, relative = name.split("/", 1)
                        if prefix == "data":
                            target = staged_data / relative
                        elif nested:
                            target = staged_data / auth.relative_to(data) / relative
                        else:
                            target = staged_auth / relative
                        target.parent.mkdir(parents=True, exist_ok=True)
                        if target.is_symlink() or any(
                            parent.is_symlink() for parent in target.parents if parent != root.parent
                        ):
                            raise BackupError("backup destination conflicts with a symbolic link")
                        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        digest = hashlib.sha256()
                        copied = 0
                        with os.fdopen(fd, "wb") as handle, archive.open(name) as source:
                            for chunk in iter(lambda: source.read(1 << 20), b""):
                                copied += len(chunk)
                                if copied > entry["bytes"]:
                                    raise BackupError("backup entry exceeds its declared size")
                                digest.update(chunk)
                                handle.write(chunk)
                        if copied != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                            raise BackupError("backup changed during staging")
            except (zipfile.BadZipFile, EOFError, RuntimeError, zlib.error):
                raise BackupError("backup changed during staging") from None

            # The archive has been fully verified *and* staged. No live data has
            # moved yet. Refuse newly active capture or changed target contents.
            if not nested and summary["includes_secrets"]:
                auth.parent.mkdir(parents=True, exist_ok=True)
                targets = [(data, staged_data), (auth, staged_auth)]
            else:
                targets = [(data, staged_data)]
            if any(target.is_symlink() or (target.exists() and not target.is_dir())
                   for target, _ in targets):
                raise BackupError("restore target changed after preflight")
            if not replace and any(
                target.is_dir() and any(p.name != ".collector.lock" for p in target.iterdir())
                for target, _ in targets
            ):
                raise BackupError("restore target was populated after preflight; use --replace")
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            moved: list[tuple[Path, Path]] = []
            replaced: list[Path] = []
            try:
                for target, source in targets:
                    if target.exists():
                        aside = target.with_name(f"{target.name}.pre-restore-{stamp}")
                        if aside.exists():
                            raise BackupError("a pre-restore directory already exists; choose another restore")
                        target.rename(aside)
                        moved.append((target, aside))
                    source.rename(target)
                    replaced.append(target)
            except Exception:
                # If any swap fails, keep any newly staged target separately for
                # inspection, then restore the old folders where possible.
                for target in reversed(replaced):
                    if target.exists():
                        failed = target.with_name(f"{target.name}.failed-restore-{stamp}")
                        try:
                            target.rename(failed)
                        except OSError:
                            pass
                rollback_failed = False
                for target, aside in reversed(moved):
                    if aside.exists() and not target.exists():
                        try:
                            aside.rename(target)
                        except OSError:
                            rollback_failed = True
                raise BackupError(
                    "restore swap failed; previous folders could not all be recovered automatically"
                    if rollback_failed else
                    "restore swap failed; previous folders were restored"
                ) from None
            return summary | {"restored_files": summary["file_count"],
                              "moved_aside": [str(aside) for _, aside in moved]}
    finally:
        if lock is not None:
            lock.release()


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
    for name in ("create", "verify", "plan", "restore"):
        command = sub.add_parser(name)
        command.add_argument("archive", type=Path)
        command.add_argument("--passphrase-env", help="environment variable holding the passphrase")
        if name in ("plan", "restore"):
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
        elif args.command == "plan":
            result = plan_restore(args.archive, passphrase=passphrase, replace=args.replace)
            print(json.dumps(result, indent=2))
            return 0 if result["ready"] else 2
        else:
            result = restore_backup(args.archive, passphrase=passphrase, replace=args.replace)
    except BackupError as exc:
        print(json.dumps({"error": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


__all__ = ["BackupError", "create_backup", "create_in_folder", "list_backups", "restore_backup",
           "verify_backup", "verify_in_folder", "plan_restore"]

if __name__ == "__main__":
    raise SystemExit(main())
