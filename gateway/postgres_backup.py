"""Operator-only PostgreSQL Gateway backup and isolated restore rehearsal.

No HTTP/MCP route, provider calls, live restore, secret arguments or automatic
promotion. A database archive contains sensitive data across all organizations.
Only run on operator-controlled hosts and rehearse trusted archives on a
dedicated, isolated PostgreSQL cluster.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

FORMAT = "owg.gateway.postgresql-backup.v1"
ARCHIVE = "gateway.pgdump"
MANIFEST = "manifest.json"
SOURCE_ENV = "OWG_GATEWAY_DATABASE_URL"
TARGET_ENV = "OWG_GATEWAY_REHEARSAL_DATABASE_URL"
MAX_MANIFEST = 64 * 1024
REQUIRED_TABLES = frozenset({"evidence_events", "access_tokens",
                             "organization_policies", "audit_log"})
CONFIRM_BACKUP = "BACKUP ALL ORGANIZATIONS"
CONFIRM_REHEARSAL = "REHEARSE ON ISOLATED CLUSTER"
# Deliberately narrow libpq connection parameters. No service file, arbitrary
# options, connection URI in argv, fallback database or implicit local socket.
LIBPQ_ENV = {
    "host": "PGHOST", "hostaddr": "PGHOSTADDR", "port": "PGPORT",
    "user": "PGUSER", "password": "PGPASSWORD", "dbname": "PGDATABASE",
    "sslmode": "PGSSLMODE", "sslrootcert": "PGSSLROOTCERT",
    "sslcert": "PGSSLCERT", "sslkey": "PGSSLKEY",
    "sslcrl": "PGSSLCRL", "sslcrldir": "PGSSLCRLDIR",
    "passfile": "PGPASSFILE", "connect_timeout": "PGCONNECT_TIMEOUT",
    "channel_binding": "PGCHANNELBINDING", "gssencmode": "PGGSSENCMODE",
}
HEX = re.compile(r"[0-9a-f]{64}")
SCRATCH = re.compile(r"owg_rehearsal_[0-9a-f]{24}")


class BackupError(RuntimeError):
    """Fixed diagnostic codes only; never include connection or SQL messages."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise BackupError(code)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def connection_parameters(value: str) -> dict[str, str]:
    from psycopg.conninfo import conninfo_to_dict
    require(isinstance(value, str) and value.startswith(("postgres://", "postgresql://")),
            "explicit_postgresql_url_required")
    try:
        params = conninfo_to_dict(value)
    except Exception:
        raise BackupError("invalid_postgresql_url") from None
    require(set(params) <= set(LIBPQ_ENV), "unsupported_connection_parameters")
    require(all(params.get(k) for k in ("host", "user", "dbname")),
            "explicit_host_user_database_required")
    require(not any("," in params.get(k, "") for k in ("host", "hostaddr", "port")),
            "multi_host_connections_not_supported")
    require(not params["host"].startswith(("/", "@")), "tcp_host_required")
    require(params.get("sslmode") in ("require", "verify-ca", "verify-full", "disable"),
            "explicit_sslmode_required")
    # 'disable' is useful only for deliberately isolated local test networks;
    # enforce deployment TLS by choosing verify-full in production.
    params.setdefault("port", "5432")
    params.setdefault("connect_timeout", "10")
    return params


def subprocess_environment(params: dict[str, str]) -> dict[str, str]:
    # Remove application and PostgreSQL secrets inherited from the operator.
    # Preserve only OS necessities; no shell, user script or .pg_service.
    permitted = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
                 "HOME", "USERPROFILE", "APPDATA", "LANG", "LC_ALL"}
    env = {k: v for k, v in os.environ.items() if k.upper() in permitted}
    env.update({LIBPQ_ENV[k]: v for k, v in params.items()})
    return env


def run_tool(name: str, args: list[str], params: dict[str, str], timeout: int) -> None:
    require(name in ("pg_dump", "pg_restore"), "unexpected_tool")
    binary = shutil.which(name)
    require(binary is not None, "postgresql_client_tool_missing")
    try:
        result = subprocess.run([binary, *args], env=subprocess_environment(params),
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise BackupError(name + "_timeout") from None
    except OSError:
        raise BackupError(name + "_launch_failed") from None
    require(result.returncode == 0, name + "_failed")


def _connect(params: dict[str, str], *, autocommit=False):
    import psycopg
    # Explicitly disable ambient libpq service/option fallbacks on this path;
    # direct connections use the same validated transport as client binaries.
    from psycopg.conninfo import make_conninfo
    return psycopg.connect(make_conninfo(**params), autocommit=autocommit)


def _ambient_libpq_guard() -> None:
    # psycopg also consults ambient libpq variables, unlike the restricted
    # subprocess environment. Reject them rather than allowing split targets
    # or hidden session options between the snapshot and pg_dump.
    require(not any(k.upper().startswith("PG") for k in os.environ),
            "ambient_libpq_environment_not_allowed")


def _private_directory(path: Path) -> None:
    require(path.is_dir() and not path.is_symlink(), "private_directory_required")
    if os.name == "posix":
        info = path.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0,
                "directory_must_be_owner_only")


def _private_file(path: Path) -> None:
    require(path.is_file() and not path.is_symlink(), "regular_bundle_file_required")
    if os.name == "posix":
        info = path.stat()
        require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) & 0o077 == 0,
                "file_must_be_owner_only")


def _new_private_file(path: Path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def digest_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as src:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def inventory(conn) -> dict[str, Any]:
    """Counts-only comparison on the same consistent database snapshot.

    Compare exact per-table counts through a canonical digest; the manifest
    contains neither table names nor individual counts. This does not prove
    content equality or validate business outcomes.
    """
    from psycopg import sql
    tables = conn.execute("""
        SELECT n.nspname, c.relname, c.relkind
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname, c.relname
    """).fetchall()
    require(REQUIRED_TABLES <= {name for schema, name, _ in tables if schema == "public"},
            "gateway_schema_missing")
    counts = []
    for schema, name, kind in tables:
        # ONLY prevents double counting inheritance or partition descendants.
        statement = sql.SQL("SELECT count(*) FROM ONLY {}").format(sql.Identifier(schema, name))
        count = conn.execute(statement).fetchone()[0] if kind == "r" else 0
        counts.append([schema, name, kind, count])
    columns = conn.execute("""
        SELECT n.nspname, c.relname, a.attnum, a.attname,
               pg_catalog.format_type(a.atttypid,a.atttypmod),
               a.attnotnull, a.attidentity, a.attgenerated,
               coalesce(pg_catalog.pg_get_expr(d.adbin,d.adrelid),'')
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid=a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname,c.relname,a.attnum
    """).fetchall()
    # Dropped columns are not dumped. Compare relative live column order rather
    # than physical attnum, which can legitimately change after logical restore.
    columns = [[r[0], r[1], *r[3:]] for r in columns]
    constraints = conn.execute("""
        SELECT n.nspname,c.relname,k.conname,k.contype,
               pg_catalog.pg_get_constraintdef(k.oid,false),k.convalidated
        FROM pg_catalog.pg_constraint k
        JOIN pg_catalog.pg_class c ON c.oid=k.conrelid
        JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE c.relkind IN ('r','p')
          AND n.nspname NOT IN ('pg_catalog','information_schema')
          AND n.nspname NOT LIKE 'pg_%'
        ORDER BY n.nspname,c.relname,k.conname
    """).fetchall()
    # Separate schema/count digests make mismatches actionable without exposing
    # tenant IDs, values, names or individual relation populations.
    return {"tables": len(tables), "rows": sum(row[-1] for row in counts),
            "row_count_sha256": canonical_digest(sorted(counts)),
            "schema_sha256": canonical_digest([sorted(columns, key=lambda x: (x[0], x[1])),
                                                sorted(constraints)])}


@contextmanager
def snapshot(params: dict[str, str]):
    with _connect(params) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        # No user schema shadows built-in functions; the inventory qualifies
        # table identifiers explicitly. Stable deparsing for logical restore.
        conn.execute("SET LOCAL search_path TO pg_catalog")
        yield conn


def validate_timeout(timeout: int) -> None:
    require(type(timeout) is int and 1 <= timeout <= 86400, "invalid_timeout")


def create_backup(destination: Path, url: str, *, confirm: str, timeout: int = 3600) -> dict:
    require(confirm == CONFIRM_BACKUP, "all_organizations_confirmation_required")
    validate_timeout(timeout)
    _ambient_libpq_guard()
    params = connection_parameters(url)
    destination = Path(destination)
    require(destination.parent.is_dir(), "destination_parent_missing")
    # Exclusive directory creation. Manifest is written last, so an interrupted
    # backup cannot pass verification. Never overwrite another backup.
    try:
        destination.mkdir(mode=0o700)
    except FileExistsError:
        raise BackupError("destination_exists") from None
    started = time.monotonic()
    try:
        _private_directory(destination)
        archive = destination / ARCHIVE
        with _new_private_file(archive):
            pass
        with snapshot(params) as conn:
            database = conn.execute("SELECT current_database(), current_setting('server_version_num')").fetchone()
            require(database[0] == params["dbname"], "source_database_mismatch")
            snap = conn.execute("SELECT pg_export_snapshot()").fetchone()[0]
            expected = inventory(conn)
            # pg_dump uses the exact exported MVCC snapshot used by inventory.
            # Its transaction cannot outlive this exporting transaction.
            run_tool("pg_dump", ["--format=custom", "--no-password", "--no-owner",
                                 "--no-acl", "--snapshot=" + snap,
                                 "--file=" + str(archive.resolve())], params, timeout)
        _private_file(archive)
        sha, size = digest_file(archive)
        require(size > 0, "empty_archive")
        manifest = {"format": FORMAT, "created_at": utcnow(),
                    "archive": ARCHIVE, "archive_sha256": sha, "archive_bytes": size,
                    "server_version_num": int(database[1]), "inventory": expected,
                    "scope": "all_organizations_database",
                    "roles_and_external_secrets_included": False,
                    "restore_rehearsed": False}
        with _new_private_file(destination / MANIFEST) as out:
            out.write((json.dumps(manifest, indent=2, allow_nan=False) + "\n").encode())
            out.flush()
            os.fsync(out.fileno())
        return {"status": "created", "manifest": manifest,
                "seconds": time.monotonic() - started}
    except BaseException:
        # Preserve an incomplete private directory for forensic inspection;
        # remove any partial manifest so it cannot be mistaken for success.
        (destination / MANIFEST).unlink(missing_ok=True)
        raise


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_manifest_key")
        result[key] = value
    return result


def _valid_digest(value: Any) -> bool:
    return isinstance(value, str) and HEX.fullmatch(value) is not None


def _nonnegative(value: Any) -> bool:
    return type(value) is int and value >= 0


def read_manifest(bundle: Path) -> dict:
    _private_directory(bundle)
    path = bundle / MANIFEST
    _private_file(path)
    with path.open("rb") as src:
        data = src.read(MAX_MANIFEST + 1)
    require(len(data) <= MAX_MANIFEST, "manifest_too_large")
    try:
        manifest = json.loads(data, object_pairs_hook=_unique_pairs)
    except (ValueError, UnicodeError):
        raise BackupError("invalid_manifest") from None
    require(isinstance(manifest, dict) and set(manifest) == {
        "format", "created_at", "archive", "archive_sha256", "archive_bytes",
        "server_version_num", "inventory", "scope",
        "roles_and_external_secrets_included", "restore_rehearsed"}, "invalid_manifest_shape")
    require(manifest["format"] == FORMAT and manifest["archive"] == ARCHIVE
            and manifest["scope"] == "all_organizations_database"
            and manifest["roles_and_external_secrets_included"] is False
            and manifest["restore_rehearsed"] is False, "unsupported_manifest")
    require(_valid_digest(manifest["archive_sha256"])
            and _nonnegative(manifest["archive_bytes"]) and manifest["archive_bytes"] > 0
            and _nonnegative(manifest["server_version_num"]), "invalid_archive_metadata")
    inv = manifest["inventory"]
    require(isinstance(inv, dict) and set(inv) == {
        "tables", "rows", "row_count_sha256", "schema_sha256"}
        and _nonnegative(inv["tables"]) and inv["tables"] >= len(REQUIRED_TABLES)
        and _nonnegative(inv["rows"]) and _valid_digest(inv["row_count_sha256"])
        and _valid_digest(inv["schema_sha256"]), "invalid_inventory")
    try:
        date = datetime.fromisoformat(manifest["created_at"])
        require(date.tzinfo is not None, "invalid_creation_time")
    except (ValueError, TypeError):
        raise BackupError("invalid_creation_time") from None
    return manifest


def verify_backup(bundle: Path) -> dict:
    bundle = Path(bundle)
    manifest = read_manifest(bundle)
    archive = bundle / ARCHIVE
    _private_file(archive)
    sha, size = digest_file(archive)
    require(size == manifest["archive_bytes"] and sha == manifest["archive_sha256"],
            "archive_integrity_mismatch")
    return {"status": "integrity_verified", "manifest": manifest,
            "authenticity_verified": False, "restore_rehearsed": False}


def _same_endpoint(a: dict, b: dict) -> bool:
    return (a["host"].lower(), a["port"]) == (b["host"].lower(), b["port"])


def rehearse_backup(bundle: Path, target_url: str, *, confirm: str,
                    timeout: int = 3600, source_url: str | None = None) -> dict:
    """Create and drop only a fresh generated database, never a supplied name."""
    require(confirm == CONFIRM_REHEARSAL, "isolated_cluster_confirmation_required")
    validate_timeout(timeout)
    _ambient_libpq_guard()
    params = connection_parameters(target_url)
    require(params["dbname"] == "postgres", "rehearsal_maintenance_database_required")
    if source_url:
        require(not _same_endpoint(connection_parameters(source_url), params),
                "source_endpoint_cannot_be_rehearsal_endpoint")
    bundle = Path(bundle)
    manifest = read_manifest(bundle)
    _private_file(bundle / ARCHIVE)
    started = time.monotonic()
    from psycopg import sql
    # Pin a verified private local copy before invoking pg_restore, avoiding a
    # hash/restore race on the original path. This can require another full
    # archive's disk space. Only trusted operator-owned archives are accepted.
    with tempfile.TemporaryDirectory(prefix="owg-restore-rehearsal-") as temp:
        pinned = Path(temp) / ARCHIVE
        with (bundle / ARCHIVE).open("rb") as src, _new_private_file(pinned) as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
        sha, size = digest_file(pinned)
        require(sha == manifest["archive_sha256"] and size == manifest["archive_bytes"],
                "archive_integrity_mismatch")
        name = "owg_rehearsal_" + secrets.token_hex(12)
        require(SCRATCH.fullmatch(name) is not None, "invalid_generated_database")
        created = False
        with _connect(params, autocommit=True) as admin:
            actual = admin.execute("SELECT current_database(), current_setting('server_version_num')").fetchone()
            require(actual[0] == "postgres", "target_database_mismatch")
            # Refuse an older server major for logical restore.
            require(int(actual[1]) // 10000 >= manifest["server_version_num"] // 10000,
                    "rehearsal_server_older_than_source")
            try:
                admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(name)))
                created = True
                target = {**params, "dbname": name}
                run_tool("pg_restore", ["--no-password", "--no-owner", "--no-acl",
                                        "--exit-on-error", "--single-transaction",
                                        "--dbname=" + name, str(pinned)], target, timeout)
                with snapshot(target) as conn:
                    require(conn.execute("SELECT current_database()").fetchone()[0] == name,
                            "scratch_database_mismatch")
                    actual_inventory = inventory(conn)
                require(actual_inventory == manifest["inventory"], "restored_inventory_mismatch")
            finally:
                if created:
                    try:
                        # No FORCE and no name from the archive/operator.
                        admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
                    except Exception:
                        raise BackupError("scratch_cleanup_failed_operator_review_required") from None
    return {"status": "rehearsal_passed", "archive_sha256": sha,
            "restored_inventory": actual_inventory, "scratch_database_removed": True,
            "seconds": time.monotonic() - started, "live_restore_performed": False,
            "authenticity_verified": False, "row_content_equality_verified": False,
            "authorization_or_revocation_reconciled": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("create", "verify", "rehearse"):
        child = sub.add_parser(command)
        child.add_argument("--bundle", type=Path, required=True)
        if command != "verify":
            child.add_argument("--confirm", required=True)
            child.add_argument("--timeout", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(args.bundle, os.environ.get(SOURCE_ENV, ""),
                                   confirm=args.confirm, timeout=args.timeout)
        elif args.command == "verify":
            result = verify_backup(args.bundle)
        else:
            result = rehearse_backup(args.bundle, os.environ.get(TARGET_ENV, ""),
                                     confirm=args.confirm, timeout=args.timeout,
                                     source_url=os.environ.get(SOURCE_ENV))
    except BackupError as exc:
        result = {"status": "failed", "error": str(exc)}
    except Exception:
        # Provider, libpq and filesystem exceptions can contain credentials,
        # SQL text, server addresses or sensitive paths. Do not print them.
        result = {"status": "failed", "error": "unexpected_operator_error"}
    print(json.dumps(result, indent=2, allow_nan=False))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
