"""Verified local backup and offline restore of all OWG data."""
from __future__ import annotations

import json
import os
import sqlite3
import zipfile
from pathlib import Path

import duckdb
import pytest

from server import local_backup as backup


@pytest.fixture
def stores(tmp_path, monkeypatch):
    data = tmp_path / "data"
    auth = data / "auth"
    auth.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("OWG_BACKUP_DIR", str(tmp_path / "owg-backups"))
    db = sqlite3.connect(data / "workflow_observer.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE events(id TEXT)")
    db.execute("INSERT INTO events VALUES ('e1')")
    db.commit()
    db.close()
    conn = duckdb.connect(str(data / "event_logs.duckdb"))
    conn.execute("CREATE TABLE logs(n INT)")
    conn.execute("INSERT INTO logs VALUES (42)")
    conn.close()
    (data / "events.jsonl").write_text('{"event_id":"e1"}\n', encoding="utf-8")
    (data / "policy_history").mkdir()
    (data / "policy_history" / "v1.json").write_text("{}", encoding="utf-8")
    (auth / ".api_token").write_text("SECRET-TOKEN", encoding="utf-8")
    return tmp_path


def test_backup_snapshots_every_store_without_secrets(stores):
    result = backup.create_backup(stores / "b.zip")
    assert result["file_count"] == 4 and result["includes_secrets"] is False
    names = set(zipfile.ZipFile(stores / "b.zip").namelist())
    assert names == {"manifest.json", "data/workflow_observer.db", "data/event_logs.duckdb",
                     "data/events.jsonl", "data/policy_history/v1.json"}
    assert b"SECRET-TOKEN" not in (stores / "b.zip").read_bytes()
    manifest = json.loads(zipfile.ZipFile(stores / "b.zip").read("manifest.json"))
    kinds = {f["path"]: f["kind"] for f in manifest["files"]}
    assert kinds["data/workflow_observer.db"] == "sqlite" and kinds["data/event_logs.duckdb"] == "duckdb"
    if os.name == "posix":
        assert oct((stores / "b.zip").stat().st_mode & 0o777) == "0o600"


def test_encrypted_backup_includes_keys_and_needs_the_passphrase(stores):
    backup.create_backup(stores / "e.zip", passphrase="correct horse battery")
    raw = (stores / "e.zip").read_bytes()
    assert raw.startswith(backup.ENCRYPTED_MAGIC) and b"SECRET-TOKEN" not in raw
    assert backup.verify_backup(stores / "e.zip", passphrase="correct horse battery")["includes_secrets"] is True
    with pytest.raises(backup.BackupError):
        backup.verify_backup(stores / "e.zip", passphrase="wrong passphrase here")
    with pytest.raises(backup.BackupError):
        backup.verify_backup(stores / "e.zip")
    with pytest.raises(backup.BackupError):
        backup.create_backup(stores / "short.zip", passphrase="short")


def test_verify_detects_tampering(stores):
    backup.create_backup(stores / "b.zip")
    source = zipfile.ZipFile(stores / "b.zip")
    with zipfile.ZipFile(stores / "t.zip", "w") as out:
        for name in source.namelist():
            body = source.read(name)
            out.writestr(name, body + b"x" if name == "data/events.jsonl" else body)
    with pytest.raises(backup.BackupError, match="changed or damaged"):
        backup.verify_backup(stores / "t.zip")


def test_restore_replaces_data_moves_old_aside_and_keeps_current_keys(stores):
    backup.create_backup(stores / "b.zip")
    data = stores / "data"
    db = sqlite3.connect(data / "workflow_observer.db")
    db.execute("INSERT INTO events VALUES ('after-backup')")
    db.commit()
    db.close()
    with pytest.raises(backup.BackupError, match="not empty"):
        backup.restore_backup(stores / "b.zip")
    result = backup.restore_backup(stores / "b.zip", replace=True)
    assert len(result["moved_aside"]) == 1 and Path(result["moved_aside"][0]).is_dir()
    rows = sqlite3.connect(data / "workflow_observer.db").execute("SELECT id FROM events").fetchall()
    assert rows == [("e1",)]
    assert duckdb.connect(str(data / "event_logs.duckdb")).execute("SELECT n FROM logs").fetchall() == [(42,)]
    assert (data / "auth" / ".api_token").read_text() == "SECRET-TOKEN"


def test_restore_refuses_newer_versions_and_unsafe_paths(stores, monkeypatch):
    backup.create_backup(stores / "b.zip")
    monkeypatch.setattr(backup, "current_version", lambda: "0.0.1")
    with pytest.raises(backup.BackupError, match="newer"):
        backup.restore_backup(stores / "b.zip", replace=True)
    manifest = {"format": backup.FORMAT, "owg_version": "0.0.1", "includes_secrets": False, "files": [
        {"path": "data/../../evil", "kind": "file", "bytes": 1, "sha256": ""}]}
    with zipfile.ZipFile(stores / "evil.zip", "w") as out:
        out.writestr("manifest.json", json.dumps(manifest))
        out.writestr("data/../../evil", "x")
    with pytest.raises(backup.BackupError):
        backup.verify_backup(stores / "evil.zip")


def test_backup_refuses_destination_inside_data_or_existing(stores):
    with pytest.raises(backup.BackupError):
        backup.create_backup(stores / "data" / "inside.zip")
    (stores / "exists.zip").write_bytes(b"")
    with pytest.raises(backup.BackupError):
        backup.create_backup(stores / "exists.zip")


def test_folder_listing_and_verification_by_name(stores):
    created = backup.create_in_folder()
    name = Path(created["archive"]).name
    listed = backup.list_backups()
    assert [item["name"] for item in listed["backups"]] == [name]
    assert backup.verify_in_folder(name)["verified"] is True
    with pytest.raises(backup.BackupError):
        backup.verify_in_folder("../" + name)


def test_privacy_tab_and_routes_offer_backup():
    root = Path(__file__).resolve().parents[1]
    script = (root / "dashboard" / "basic_mode.js").read_text(encoding="utf-8")
    assert 'id="pvBackupCreate"' in script and "/v1/local-backups" in script
    routes = (root / "server" / "basic_mode_routes.py").read_text(encoding="utf-8")
    assert '"/v1/local-backups", get_local_backups' in routes and '"/v1/local-backups", post_local_backup' in routes
