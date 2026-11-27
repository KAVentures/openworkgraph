"""Operator backup safety tests and opt-in real PostgreSQL recovery rehearsal."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat

import pytest

from gateway import postgres_backup as backup


def fake_bundle(tmp_path: Path):
    folder = tmp_path / "bundle"
    folder.mkdir(mode=0o700)
    archive = folder / backup.ARCHIVE
    archive.write_bytes(b"synthetic archive bytes, not a PostgreSQL archive")
    archive.chmod(0o600)
    sha, size = backup.digest_file(archive)
    manifest = {
        "format": backup.FORMAT, "created_at": "2026-01-01T00:00:00+00:00",
        "archive": backup.ARCHIVE, "archive_sha256": sha, "archive_bytes": size,
        "server_version_num": 160000, "scope": "all_organizations_database",
        "inventory": {"tables": 4, "rows": 0, "row_count_sha256": "a" * 64,
                      "schema_sha256": "b" * 64},
        "roles_and_external_secrets_included": False, "restore_rehearsed": False,
    }
    write_manifest(folder, manifest)
    return folder, manifest


def write_manifest(folder, manifest):
    path = folder / backup.MANIFEST
    path.write_text(json.dumps(manifest), encoding="utf-8")
    path.chmod(0o600)


def test_integrity_is_not_authenticity_or_recovery(tmp_path):
    folder, manifest = fake_bundle(tmp_path)
    result = backup.verify_backup(folder)
    assert result == {"status": "integrity_verified", "manifest": manifest,
                      "authenticity_verified": False, "restore_rehearsed": False}
    (folder / backup.ARCHIVE).write_bytes(b"modified")
    with pytest.raises(backup.BackupError, match="archive_integrity_mismatch"):
        backup.verify_backup(folder)


@pytest.mark.parametrize(("field", "value"), [
    ("archive", "../outside"), ("archive", "/tmp/other"),
    ("format", "other"), ("archive_bytes", True), ("archive_bytes", -1),
    ("archive_sha256", "z" * 64), ("scope", "single_organization"),
    ("restore_rehearsed", True), ("created_at", "2026-01-01T00:00:00"),
    ("inventory", {"tables": 4, "rows": 1}),
    ("roles_and_external_secrets_included", True),
])
def test_manifest_refuses_incompatible_or_unsafe_fields(tmp_path, field, value):
    folder, manifest = fake_bundle(tmp_path)
    manifest[field] = value
    write_manifest(folder, manifest)
    with pytest.raises(backup.BackupError):
        backup.verify_backup(folder)


def test_incomplete_or_duplicate_key_manifest_refused(tmp_path):
    folder, _ = fake_bundle(tmp_path)
    path = folder / backup.MANIFEST
    path.write_text('{"format":1,"format":2}', encoding="utf-8")
    with pytest.raises(backup.BackupError, match="duplicate_manifest_key"):
        backup.verify_backup(folder)
    path.write_bytes(b" " * (backup.MAX_MANIFEST + 1))
    with pytest.raises(backup.BackupError, match="manifest_too_large"):
        backup.verify_backup(folder)
    path.unlink()
    with pytest.raises(backup.BackupError, match="regular_bundle_file_required"):
        backup.verify_backup(folder)


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner-only permissions")
def test_public_or_symlink_archive_is_refused(tmp_path):
    folder, _ = fake_bundle(tmp_path)
    path = folder / backup.ARCHIVE
    path.chmod(0o644)
    with pytest.raises(backup.BackupError, match="file_must_be_owner_only"):
        backup.verify_backup(folder)
    path.unlink()
    outside = tmp_path / "outside"
    outside.write_bytes(b"secret")
    path.symlink_to(outside)
    with pytest.raises(backup.BackupError, match="regular_bundle_file_required"):
        backup.verify_backup(folder)


def test_confirmations_before_database_or_filesystem(tmp_path):
    destination = tmp_path / "never-created"
    with pytest.raises(backup.BackupError, match="all_organizations_confirmation_required"):
        backup.create_backup(destination, "", confirm="")
    assert not destination.exists()
    with pytest.raises(backup.BackupError, match="isolated_cluster_confirmation_required"):
        backup.rehearse_backup(destination, "", confirm="")


def test_environment_excludes_other_app_and_libpq_secrets(monkeypatch):
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN", "application-secret")
    monkeypatch.setenv("PGOPTIONS", "-c search_path=untrusted")
    monkeypatch.setenv("PGPASSWORD", "ambient-password")
    env = backup.subprocess_environment({"host": "db", "password": "intended-secret",
                                         "dbname": "gateway", "sslmode": "verify-full"})
    assert env["PGPASSWORD"] == "intended-secret"
    assert env["PGDATABASE"] == "gateway"
    assert "PGOPTIONS" not in env and "OWG_GATEWAY_ADMIN_TOKEN" not in env
    with pytest.raises(backup.BackupError, match="ambient_libpq_environment_not_allowed"):
        backup._ambient_libpq_guard()


def test_tool_errors_are_minimized_and_no_shell_or_password_argument(monkeypatch):
    import subprocess
    calls = []
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/" + name)

    def fake_run(args, **kw):
        calls.append((args, kw))
        return subprocess.CompletedProcess(args, 1, stderr=b"private-database-detail")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(backup.BackupError, match="^pg_dump_failed$"):
        backup.run_tool("pg_dump", ["--format=custom"],
                        {"password": "sensitive-password", "dbname": "db"}, 5)
    args, kwargs = calls[0]
    assert all("sensitive-password" not in arg for arg in args)
    assert kwargs["env"]["PGPASSWORD"] == "sensitive-password"
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert not kwargs.get("shell", False)


@pytest.mark.parametrize("url", [
    "sqlite:///local.db", "postgresql:///gateway",
    "postgresql://user@db/gateway",
    "postgresql://user@db/gateway?sslmode=prefer",
    "postgresql://user@db/gateway?sslmode=require&options=-c%20search_path%3Dother",
    "postgresql://user@one,two/gateway?sslmode=require",
    "postgresql://user@%2Ftmp/gateway?sslmode=require",
])
def test_reject_ambiguous_connections(url):
    pytest.importorskip("psycopg")
    with pytest.raises(backup.BackupError):
        backup.connection_parameters(url)


def test_connection_and_same_endpoint_guard(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    src = "postgresql://user:example@database/gateway?sslmode=verify-full"
    target = "postgresql://admin:other@DATABASE:5432/postgres?sslmode=verify-full"
    params = backup.connection_parameters(src)
    assert params["dbname"] == "gateway" and params["port"] == "5432"
    assert backup._same_endpoint(params, backup.connection_parameters(target))
    with pytest.raises(backup.BackupError, match="source_endpoint_cannot_be_rehearsal_endpoint"):
        backup.rehearse_backup(tmp_path, target, confirm=backup.CONFIRM_REHEARSAL,
                               source_url=src)
    with pytest.raises(backup.BackupError, match="rehearsal_maintenance_database_required"):
        backup.rehearse_backup(tmp_path, src, confirm=backup.CONFIRM_REHEARSAL)


def test_existing_destination_is_never_overwritten(tmp_path, monkeypatch):
    pytest.importorskip("psycopg")
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    folder, manifest = fake_bundle(tmp_path)
    with pytest.raises(backup.BackupError, match="destination_exists"):
        backup.create_backup(folder, "postgresql://user@db/gateway?sslmode=require",
                             confirm=backup.CONFIRM_BACKUP)
    assert backup.verify_backup(folder)["manifest"] == manifest


def test_real_postgres_snapshot_and_isolated_rehearsal(tmp_path, monkeypatch):
    source = os.environ.get("OWG_BACKUP_TEST_SOURCE_URL")
    target = os.environ.get("OWG_BACKUP_TEST_REHEARSAL_URL")
    if not source or not target:
        pytest.skip("requires two isolated synthetic PostgreSQL clusters")
    pytest.importorskip("psycopg")
    import psycopg
    from gateway.db import GatewayDB
    for key in list(os.environ):
        if key.upper().startswith("PG"):
            monkeypatch.delenv(key)
    # This test is intentionally opt-in and accepts only specifically named
    # fixture databases, not the operator's usual Gateway environment.
    assert backup.connection_parameters(source)["dbname"] == "owg_backup_fixture"
    assert backup.connection_parameters(target)["dbname"] == "postgres"
    assert not backup._same_endpoint(backup.connection_parameters(source),
                                     backup.connection_parameters(target))
    db = GatewayDB(source)
    db.init()
    db.put_token(token_id="backup-fixture-token", token="synthetic-secret",
                 token_type="integration", organization_id="fixture-org",
                 scopes={"evidence:read"})
    assert db.revoke_token("backup-fixture-token")
    with psycopg.connect(source) as conn:
        conn.execute("CREATE TABLE backup_fixture_extra (id int PRIMARY KEY, label text NOT NULL)")
        conn.execute("INSERT INTO backup_fixture_extra VALUES (1,'one'),(2,'two')")
    path = tmp_path / "backup"
    report = backup.create_backup(path, source, confirm=backup.CONFIRM_BACKUP, timeout=120)
    assert report["status"] == "created"
    assert backup.verify_backup(path)["status"] == "integrity_verified"
    restored = backup.rehearse_backup(path, target, confirm=backup.CONFIRM_REHEARSAL,
                                      source_url=source, timeout=120)
    assert restored["status"] == "rehearsal_passed"
    assert restored["restored_inventory"] == report["manifest"]["inventory"]
    assert restored["scratch_database_removed"]
    assert not restored["live_restore_performed"]
    with psycopg.connect(target) as conn:
        assert not conn.execute("SELECT datname FROM pg_database WHERE left(datname,14)='owg_rehearsal_'").fetchall()
    # Source remains unchanged and is not promoted, initialized or rewritten.
    assert db.authenticate("synthetic-secret") is None
    with psycopg.connect(source) as conn:
        assert conn.execute("SELECT count(*) FROM backup_fixture_extra").fetchone()[0] == 2
