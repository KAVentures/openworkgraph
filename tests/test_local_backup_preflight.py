"""Offline restore preflight and hostile-archive safety regressions (#370 M7)."""
from __future__ import annotations

import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest

from server import local_backup as backup

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def site(tmp_path, monkeypatch):
    data = tmp_path / "data"
    auth = data / "auth"
    auth.mkdir(parents=True)
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    db = sqlite3.connect(data / "workflow_observer.db")
    db.execute("CREATE TABLE events(id TEXT)")
    db.execute("INSERT INTO events VALUES ('before')")
    db.commit()
    db.close()
    (auth / ".api_token").write_text("a test-only synthetic token", encoding="utf-8")
    archive = tmp_path / "backup.zip"
    backup.create_backup(archive)
    return tmp_path, data, auth, archive


def _forge(source: Path, target: Path, mutate, *, extra=None):
    with zipfile.ZipFile(source) as input_archive:
        values = [(item, input_archive.read(item.filename)) for item in input_archive.infolist()]
    with zipfile.ZipFile(target, "w") as output:
        for item, body in values:
            if item.filename == backup.MANIFEST:
                manifest = json.loads(body)
                mutate(manifest)
                body = json.dumps(manifest).encode("utf-8")
            output.writestr(item, body)
        if extra is not None:
            filename, payload, info = extra
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                output.writestr(info if info is not None else filename, payload)
    return target


def test_preflight_reports_replacement_without_mutating_existing_store(site):
    parent, data, auth, archive = site
    files_before = sorted(str(p.relative_to(data)) for p in data.rglob("*"))
    plan = backup.plan_restore(archive)
    assert plan["verified"] is True
    assert plan["ready"] is False and plan["requires_replace"] is True
    assert "--replace" in plan["blockers"][0]
    assert plan["operation"] == "offline_restore_preflight_only"
    assert plan["writes_performed"] is False
    assert not (data / ".collector.lock").exists()
    assert sorted(str(p.relative_to(data)) for p in data.rglob("*")) == files_before
    assert (auth / ".api_token").read_text() == "a test-only synthetic token"
    allowed = backup.plan_restore(archive, replace=True)
    assert allowed["ready"] is True and allowed["blockers"] == []
    assert allowed["auth_inside_data"] is True
    assert allowed["includes_secrets"] is False
    assert not (data / ".collector.lock").exists()


def test_restore_uses_preflight_and_preserves_existing_keys(site):
    parent, data, auth, archive = site
    db = sqlite3.connect(data / "workflow_observer.db")
    db.execute("INSERT INTO events VALUES ('after')")
    db.commit()
    db.close()
    with pytest.raises(backup.BackupError, match="explicitly select --replace"):
        backup.restore_backup(archive)
    result = backup.restore_backup(archive, replace=True)
    assert result["restored_files"] == result["file_count"] == 1
    assert result["ready"] is True
    assert result["operation"] == "offline_restore" and result["writes_performed"] is True
    assert len(result["moved_aside"]) == 1
    assert sqlite3.connect(data / "workflow_observer.db").execute(
        "SELECT id FROM events ORDER BY id").fetchall() == [("before",)]
    assert (auth / ".api_token").read_text() == "a test-only synthetic token"


def test_running_recorder_blocks_preflight_and_restore_without_touching_data(site):
    from collector.instance_lock import CollectorLock

    parent, data, _auth, archive = site
    lock = CollectorLock(data)
    assert lock.acquire()
    try:
        status = backup.plan_restore(archive, replace=True)
        assert status["ready"] is False
        assert any("recorder is running" in reason for reason in status["blockers"])
        with pytest.raises(backup.BackupError, match="recorder is running"):
            backup.restore_backup(archive, replace=True)
        assert (data / "workflow_observer.db").is_file()
    finally:
        lock.release()


def test_missing_lock_file_probe_is_read_only_and_empty_data_can_restore(tmp_path, monkeypatch):
    root = tmp_path / "empty"
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(root))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(root / "auth"))
    root.mkdir()
    source = tmp_path / "archive.zip"
    backup.create_backup(source)
    ready = backup.plan_restore(source)
    assert ready["ready"] and not ready["requires_replace"]
    assert list(root.iterdir()) == []
    done = backup.restore_backup(source)
    assert done["restored_files"] == 0


@pytest.mark.parametrize("mutate,expected", [
    (lambda m: m.update(file_count=m["file_count"] + 1), "file count"),
    (lambda m: m.update(total_bytes=m["total_bytes"] + 1), "total size"),
    (lambda m: m["files"][0].update(bytes=m["files"][0]["bytes"] + 1), "sizes"),
    (lambda m: m["files"][0].update(sha256="garbage"), "digest"),
    (lambda m: m["files"][0].update(path="data/../../escaped"), "unsafe"),
    (lambda m: m["files"][0].update(path="data\\windows-path"), "unsafe"),
    (lambda m: m["files"][0].update(path="data/same:alternate"), "unsafe"),
    (lambda m: m["files"][0].update(kind="symlink"), "metadata"),
    (lambda m: m.update(encrypted=True), "encryption metadata"),
    (lambda m: m.update(includes_secrets="yes"), "encryption metadata"),
    (lambda m: m.update(owg_version=None), "version"),
    (lambda m: m.update(files=m["files"] + [dict(m["files"][0])], file_count=m["file_count"]+1,
                        total_bytes=m["total_bytes"]*2), "duplicate"),
])
def test_invalid_manifest_is_refused_before_restore(site, mutate, expected):
    root, data, auth, archive = site
    malformed = _forge(archive, root / "invalid.zip", mutate)
    with pytest.raises(backup.BackupError, match=expected):
        backup.verify_backup(malformed)
    original = (data / "workflow_observer.db").read_bytes()
    with pytest.raises(backup.BackupError):
        backup.restore_backup(malformed, replace=True)
    assert (data / "workflow_observer.db").read_bytes() == original
    assert not list(root.glob("data.pre-restore-*"))


def test_duplicate_zip_member_is_detected_even_when_set_of_names_matches(site):
    root, _data, _auth, archive = site
    with zipfile.ZipFile(archive) as source:
        member = next(name for name in source.namelist() if name.startswith("data/"))
        contents = source.read(member)
    _forge(archive, root / "duplicate.zip", lambda m: None, extra=(member, contents, None))
    with pytest.raises(backup.BackupError, match="duplicate entries"):
        backup.verify_backup(root / "duplicate.zip")


def test_symlink_zip_member_is_refused_even_with_matching_hash_and_size(site):
    root, _data, _auth, archive = site
    member = "data/workflow_observer.db"
    with zipfile.ZipFile(archive) as source:
        contents = source.read(member)
        manifest = json.loads(source.read(backup.MANIFEST))
    link = zipfile.ZipInfo(member)
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(root / "symlink.zip", "w") as output:
        output.writestr(backup.MANIFEST, json.dumps(manifest))
        output.writestr(link, contents)
    with pytest.raises(backup.BackupError, match="sizes do not match"):
        backup.verify_backup(root / "symlink.zip")


def test_member_size_budget_is_checked_before_decompression(site, monkeypatch):
    root, _data, _auth, archive = site
    monkeypatch.setattr(backup, "MAX_ENTRY_BYTES", 20)
    with pytest.raises(backup.BackupError, match="oversized entry"):
        backup.verify_backup(archive)


def test_plain_zip_verification_is_file_backed_and_streams_members(site, monkeypatch):
    _root, _data, _auth, archive = site
    original = Path.read_bytes

    def refuse_full_archive_read(self):
        if self == archive:
            raise AssertionError("plain ZIP must not be loaded into RAM")
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", refuse_full_archive_read)
    assert backup.verify_backup(archive)["verified"] is True


def test_preflight_cli_returns_actionable_status_and_exit_codes(site):
    root, data, _auth, archive = site
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(data),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(data / "auth")})
    command = [sys.executable, "-m", "server.local_backup", "plan", str(archive)]
    rejected = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert rejected.returncode == 2, rejected.stderr
    status = json.loads(rejected.stdout)
    assert not status["ready"] and "--replace" in status["blockers"][0]
    accepted = subprocess.run(command + ["--replace"], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=30)
    assert accepted.returncode == 0, accepted.stderr
    assert json.loads(accepted.stdout)["writes_performed"] is False
    assert not list(root.glob("data.pre-restore-*"))


def test_staging_failure_does_not_move_existing_data(site, monkeypatch):
    root, data, _auth, archive = site
    before = (data / "workflow_observer.db").read_bytes()
    real_open = backup.os.open

    def fail_stage(name, flags, *args, **kwargs):
        if ".owg-restore-stage-" in str(name) and str(name).endswith("workflow_observer.db"):
            raise OSError("synthetic disk full")
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(backup.os, "open", fail_stage)
    with pytest.raises(backup.BackupError, match="could not be staged"):
        backup.restore_backup(archive, replace=True)
    assert (data / "workflow_observer.db").read_bytes() == before
    assert not list(root.glob("data.pre-restore-*"))


def test_swap_failure_restores_both_previous_data_and_external_auth(tmp_path, monkeypatch):
    data, auth = tmp_path / "data", tmp_path / "credential-store"
    data.mkdir()
    auth.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    (data / "sample.txt").write_text("BACKUP-VALUE", encoding="utf-8")
    (auth / "token.txt").write_text("BACKUP-TOKEN", encoding="utf-8")
    source = tmp_path / "secret-backup.zip"
    secret = "synthetic passphrase for recovery"
    backup.create_backup(source, passphrase=secret)
    (data / "sample.txt").write_text("CURRENT-DATA", encoding="utf-8")
    (auth / "token.txt").write_text("CURRENT-AUTH", encoding="utf-8")
    real_rename = Path.rename
    failed = False

    def fail_once(self, destination):
        nonlocal failed
        if (not failed and ".owg-restore-auth-" in str(self) and
                self.name == "auth" and Path(destination) == auth):
            failed = True
            raise OSError("synthetic authorization-store swap fault")
        return real_rename(self, destination)

    monkeypatch.setattr(Path, "rename", fail_once)
    with pytest.raises(backup.BackupError, match="previous folders were restored"):
        backup.restore_backup(source, passphrase=secret, replace=True)
    assert failed is True
    assert (data / "sample.txt").read_text() == "CURRENT-DATA"
    assert (auth / "token.txt").read_text() == "CURRENT-AUTH"
    assert not list(tmp_path.glob("*.pre-restore-*"))


def test_encrypted_external_credentials_restore_after_successful_stage(tmp_path, monkeypatch):
    data, auth = tmp_path / "data", tmp_path / "credential-store"
    data.mkdir()
    auth.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    (data / "sample.txt").write_text("BACKUP-VALUE", encoding="utf-8")
    (auth / "token.txt").write_text("BACKUP-TOKEN", encoding="utf-8")
    source = tmp_path / "secret-backup.zip"
    secret = "synthetic passphrase for recovery"
    backup.create_backup(source, passphrase=secret)
    (data / "sample.txt").write_text("CHANGED", encoding="utf-8")
    (auth / "token.txt").write_text("CHANGED", encoding="utf-8")
    plan = backup.plan_restore(source, passphrase=secret, replace=True)
    assert plan["ready"] and plan["includes_secrets"] and not plan["auth_inside_data"]
    done = backup.restore_backup(source, passphrase=secret, replace=True)
    assert done["restored_files"] == 2
    assert len(done["moved_aside"]) == 2
    assert (data / "sample.txt").read_text() == "BACKUP-VALUE"
    assert (auth / "token.txt").read_text() == "BACKUP-TOKEN"


def test_valid_encrypted_nested_auth_restore_recovers_keys_and_data(site):
    root, data, auth, _plain = site
    secret = "nested credentials test passphrase"
    archive = root / "full-secret.zip"
    backup.create_backup(archive, passphrase=secret)
    (auth / ".api_token").write_text("replaced later", encoding="utf-8")
    db = sqlite3.connect(data / "workflow_observer.db")
    db.execute("INSERT INTO events VALUES ('later')")
    db.commit()
    db.close()
    result = backup.restore_backup(archive, passphrase=secret, replace=True)
    assert result["includes_secrets"] is True
    assert (auth / ".api_token").read_text() == "a test-only synthetic token"
    assert sqlite3.connect(data / "workflow_observer.db").execute(
        "SELECT id FROM events ORDER BY id").fetchall() == [("before",)]


def test_forged_plaintext_credentials_under_data_prefix_are_refused(site):
    root, data, auth, archive = site
    token = b"synthetic credential that should never be stored in plaintext"
    forged = root / "disguised-auth.zip"
    with zipfile.ZipFile(archive) as original:
        source_manifest = json.loads(original.read(backup.MANIFEST))
        data_entries = [(name, original.read(name)) for name in original.namelist()
                        if name != backup.MANIFEST]
    import hashlib
    source_manifest["files"].append({
        "path": "data/auth/.api_token", "kind": "file",
        "bytes": len(token), "sha256": hashlib.sha256(token).hexdigest(),
    })
    source_manifest["file_count"] += 1
    source_manifest["total_bytes"] += len(token)
    with zipfile.ZipFile(forged, "w") as output:
        output.writestr(backup.MANIFEST, json.dumps(source_manifest))
        for name, content in data_entries:
            output.writestr(name, content)
        output.writestr("data/auth/.api_token", token)
    with pytest.raises(backup.BackupError, match="unencrypted backup cannot contain credentials"):
        backup.verify_backup(forged)
    assert (auth / ".api_token").read_text() == "a test-only synthetic token"


def test_empty_archive_cannot_erase_existing_nonempty_store(tmp_path, monkeypatch):
    from io import BytesIO

    original_dir = tmp_path / "empty-source"
    original_dir.mkdir()
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(original_dir))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(original_dir / "auth"))
    archive = tmp_path / "empty.zip"
    backup.create_backup(archive)
    assert backup.verify_backup(archive)["file_count"] == 0
    live = tmp_path / "live"
    live.mkdir()
    (live / "work.txt").write_text("DO NOT ERASE", encoding="utf-8")
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(live))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(live / "auth"))
    plan = backup.plan_restore(archive, replace=True)
    assert plan["ready"] is False
    assert "empty backup" in "; ".join(plan["blockers"])
    with pytest.raises(backup.BackupError, match="empty backup"):
        backup.restore_backup(archive, replace=True)
    assert (live / "work.txt").read_text() == "DO NOT ERASE"
