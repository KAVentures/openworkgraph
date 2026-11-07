"""Scheduled local backups: opt-in, repeatable verified snapshots, retention, and no leaks."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import threading

import pytest

from server import local_scheduler as scheduler
from server import local_backup, local_backup_schedule as automated


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    auth = data / "auth"
    auth.mkdir(parents=True)
    (data / "notes.txt").write_text("synthetic captured activity", encoding="utf-8")
    (auth / ".api_token").write_text("SYNTHETIC-SECRET", encoding="utf-8")
    sqlite = sqlite3.connect(data / "workflow_observer.db")
    sqlite.execute("CREATE TABLE events(id TEXT)")
    sqlite.execute("INSERT INTO events VALUES ('one')")
    sqlite.commit()
    sqlite.close()
    backups = tmp_path / "backups"
    monkeypatch.setattr(scheduler, "DATA_DIR", data)
    monkeypatch.setenv("WORKFLOW_OBSERVER_DATA", str(data))
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(auth))
    monkeypatch.setenv("OWG_BACKUP_DIR", str(backups))
    # These are isolated stores only; no collector, other worker, or external service.
    yield tmp_path


def make(**overrides):
    options = {"interval_minutes": 1440, "confirm_scheduled_runs": True,
               "confirm_unencrypted_local_backup": True}
    return scheduler.create("local_backup", "local", **{**options, **overrides})


def test_unconfigured_and_unacknowledged_backup_never_runs(store):
    assert not scheduler.status()["schedules"]
    assert "local_backup" in scheduler.status()["kinds"]
    assert not (store / "backups").exists()
    for args in ({"confirm_scheduled_runs": False},
                 {"confirm_unencrypted_local_backup": False},
                 {"confirm_unencrypted_local_backup": None},
                 {"interval_minutes": 1439}, {"interval_minutes": True},
                 {"interval_minutes": 10081}):
        with pytest.raises(scheduler.ScheduleError):
            make(**args)
    with pytest.raises(scheduler.ScheduleError, match="target"):
        scheduler.create("local_backup", "someone_else", interval_minutes=1440,
                         confirm_scheduled_runs=True, confirm_unencrypted_local_backup=True)
    assert not (store / "backups").exists()
    assert scheduler.status()["schedules"] == []


def test_schedulable_job_binds_one_local_target_and_is_disabled_by_pause(store):
    item = make()
    assert item["enabled"] is True
    assert item["target_id"] == "local"
    assert item["unencrypted_local_backup_acknowledged"] is True
    assert item["interval_minutes"] == 1440
    assert not (store / "backups").exists()  # creating schedule is not running it
    with pytest.raises(scheduler.ScheduleError, match="already"):
        make()
    with pytest.raises(scheduler.ScheduleError, match="1440"):
        scheduler.update(item["schedule_id"], interval_minutes=60)
    assert scheduler.update(item["schedule_id"], enabled=False)["enabled"] is False
    assert scheduler.run_due(now=10**10) == []
    assert not (store / "backups").exists()
    assert scheduler.update(item["schedule_id"], enabled=True)["enabled"] is True
    assert scheduler.delete(item["schedule_id"]) is True
    assert scheduler.run_due(now=10**10) == []


def test_due_run_creates_verified_local_non_secret_archive_and_reports_no_path(store):
    item = make()
    assert scheduler.run_due(now=0) == []
    ran = scheduler.run_due(now=10**10)
    assert ran == [{"schedule_id": item["schedule_id"], "outcome": "succeeded:local_backup_verified"}]
    files = list((store / "backups").glob("owg-backup-scheduled-*.zip"))
    assert len(files) == 1
    receipt = local_backup.verify_backup(files[0])
    assert receipt["verified"] is True
    assert receipt["encrypted"] is False and receipt["includes_secrets"] is False
    assert receipt["file_count"] >= 2
    assert automated._scheduled_archives(store / "backups") == files
    raw = files[0].read_bytes()
    assert b"SYNTHETIC-SECRET" not in raw
    state = scheduler.status()["schedules"][0]
    assert state["last_result"] == "succeeded:local_backup_verified"
    assert "backups" not in str(state) and "SYNTHETIC-SECRET" not in str(state)
    assert not local_backup.auth_dir().joinpath(".api_token").read_text() == ""


def test_rotation_only_removes_verified_scheduled_files_never_manual_or_symlink(store, monkeypatch):
    folder = store / "backups"
    folder.mkdir()
    manual = local_backup.create_backup(folder / "owg-backup-manual-saved.zip")
    manual_path = Path(manual["archive"])
    preserved = folder / "owg-backup-other-system.zip"
    preserved.write_bytes(b"not a scheduled backup")
    class Clock:
        n = 0
        @classmethod
        def now(cls, tz):
            cls.n += 1
            return datetime(2026, 11, 7, tzinfo=timezone.utc) + timedelta(seconds=cls.n)
    monkeypatch.setattr(automated, "datetime", Clock)
    outside = store / "outside.txt"
    outside.write_text("must never delete", encoding="utf-8")
    link = folder / "owg-backup-scheduled-20200101T000000000000000000Z.zip"
    if os.name != "nt":
        link.symlink_to(outside)
    for _ in range(18):
        report = automated.run()
        assert report["verified"] is True and report["local_only"] is True
        assert report["retained_scheduled_backups"] <= automated.KEEP_SCHEDULED
    files = automated._scheduled_archives(folder)
    assert len(files) == automated.KEEP_SCHEDULED
    assert files == sorted(files, reverse=True)
    assert manual_path.is_file() and preserved.is_file()
    assert outside.read_text() == "must never delete"
    if os.name != "nt":
        assert link.is_symlink()
    assert len(local_backup.list_backups()["backups"]) >= 14


def test_unsafe_backup_folder_fails_closed_with_no_scheduled_output(store, monkeypatch):
    item = make()
    monkeypatch.setenv("OWG_BACKUP_DIR", str(store / "data" / "archives"))
    with pytest.raises(scheduler.ScheduleError, match="folder"):
        scheduler._check_target("local_backup", "local")
    # A schedule previously created with a safe location also refuses to
    # create a snapshot once the location is changed to somewhere unsafe.
    assert scheduler.run_due(now=10**10) == [{"schedule_id": item["schedule_id"],
                                               "outcome": "removed_target_gone"}]
    assert not (store / "data" / "archives").exists()


def test_background_backup_failure_has_fixed_diagnostic_and_backoff(store, monkeypatch):
    item = make()
    def bad_run():
        raise automated.ScheduledBackupError("SYNTHETIC-SECRET /private/failure")
    monkeypatch.setattr(automated, "run", bad_run)
    ran = scheduler.run_due(now=10**10)
    assert ran[0]["outcome"] == "failed:local_backup_unavailable"
    state = scheduler.status()["schedules"][0]
    assert state["consecutive_failures"] == 1
    assert state["next_run_at"]
    assert "SYNTHETIC-SECRET" not in str(state)
    assert not (store / "backups").exists()


def test_other_existing_schedule_kinds_unchanged_and_no_unencrypted_ack_needed(store, monkeypatch):
    monkeypatch.setattr(scheduler, "_check_target", lambda k, t: None)
    task = scheduler.create("event_log_signals", "log_sample",
                            interval_minutes=15, confirm_scheduled_runs=True)
    assert task["kind"] == "event_log_signals"
    assert "unencrypted_local_backup_acknowledged" not in task
    assert scheduler.delete(task["schedule_id"])


def test_lookalike_and_replaced_scheduled_archive_are_never_rotated(store, monkeypatch):
    folder = store / "backups"
    folder.mkdir()
    # A manual file can deliberately look identical to a scheduled archive.
    manual_lookalike = folder / "owg-backup-scheduled-20200101T000000000000000000Z.zip"
    manual_lookalike.write_bytes(b"manually placed, not owned by scheduler")
    class Clock:
        n = 0
        @classmethod
        def now(cls, tz):
            cls.n += 1
            return datetime(2026, 11, 7, tzinfo=timezone.utc) + timedelta(seconds=cls.n)
    monkeypatch.setattr(automated, "datetime", Clock)
    automated.run()
    altered = automated._scheduled_archives(folder)[0]
    altered.write_bytes(b"manual replacement of a previously owned archive")
    for _ in range(17):
        automated.run()
    assert manual_lookalike.read_bytes() == b"manually placed, not owned by scheduler"
    assert altered.read_bytes() == b"manual replacement of a previously owned archive"
    assert len(automated._scheduled_archives(folder)) == 14
    assert len(automated._load(folder)) == 15  # modified old archive kept for review


def test_damaged_ownership_ledger_blocks_new_snapshot_and_never_prunes(store):
    folder = store / "backups"
    folder.mkdir()
    (folder / ".owg-scheduled-backups-v1.json").write_text(
        '{"version":1,"archives":[{"name":"../escape","bytes":1,"mtime_ns":1}]}',
        encoding="utf-8",
    )
    with pytest.raises(automated.ScheduledBackupError, match="ledger_invalid"):
        automated.run()
    assert not list(folder.glob("owg-backup-scheduled-*.zip"))


def test_external_ledger_symlink_is_not_read_or_overwritten(store):
    if os.name == "nt":
        pytest.skip("Windows symlink privileges vary")
    folder = store / "backups"
    folder.mkdir()
    external = store / "private-outside.json"
    external.write_text('{"some_other_configuration":true}', encoding="utf-8")
    link = folder / ".owg-scheduled-backups-v1.json"
    link.symlink_to(external)
    with pytest.raises(automated.ScheduledBackupError, match="ledger_invalid"):
        automated.run()
    assert external.read_text() == '{"some_other_configuration":true}'
    assert link.is_symlink()
