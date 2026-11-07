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
