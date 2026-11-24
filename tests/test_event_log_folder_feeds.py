"""Folder feeds append new CSV exports from a local folder to a saved log."""
from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path

import pytest

from server import event_log_folder_feeds as feeds
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

MAPPING = {"case_column": "Order", "activity_column": "Step", "timestamp_column": "When", "delimiter": ";",
           "timestamp_format": "%d/%m/%Y %H:%M", "naive_timestamp_offset": "+01:00"}
HEADER = "Order;Step;When\n"


@pytest.fixture(autouse=True)
def _data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path / "data")
    (tmp_path / "data").mkdir()
    monkeypatch.setattr(scheduler, "_path", lambda: tmp_path / "data" / "schedules.json")


def _log():
    text = HEADER + "o1;Create;01/03/2024 09:00\no1;Ship;02/03/2024 09:00\n"
    return workspace.import_log(BytesIO(text.encode()), name="orders", source_format="csv", mapping=MAPPING)["log_id"]


def _drop(folder: Path, name: str, text: str, age: float = 600):
    path = folder / name
    # Preserve exact fixture bytes on Windows (text mode converts LF to CRLF).
    path.write_bytes(text.encode("utf-8"))
    old = time.time() - age
    os.utime(path, (old, old))
    return path


def test_feed_appends_new_settled_files_once(tmp_path):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    with pytest.raises(workspace.EventLogWorkspaceError, match="confirmation"):
        feeds.create(log, {"folder": str(folder)})
    with pytest.raises(workspace.EventLogWorkspaceError, match="absolute"):
        feeds.create(log, {"folder": "exports", "confirm_folder_reads": True})
    with pytest.raises(workspace.EventLogWorkspaceError, match="pattern"):
        feeds.create(log, {"folder": str(folder), "pattern": "../*.csv", "confirm_folder_reads": True})
    feed = feeds.create(log, {"folder": str(folder), "pattern": "orders-*.csv", "confirm_folder_reads": True})
    assert feed["mapping"]["timestamp_format"] == "%d/%m/%Y %H:%M" and feed["mapping"]["delimiter"] == ";"
    with pytest.raises(workspace.EventLogWorkspaceError, match="already has a feed"):
        feeds.create(log, {"folder": str(folder), "pattern": "orders-*.csv", "confirm_folder_reads": True})

    # Overlapping export: o1/Ship is already in the log and skipped; o2 is new.
    _drop(folder, "orders-0301.csv", HEADER + "o1;Ship;02/03/2024 09:00\no2;Create;03/03/2024 10:00\n")
    _drop(folder, "orders-0302.csv", HEADER + "o2;Ship;04/03/2024 10:00\n", age=5)  # still being written
    _drop(folder, "notes.csv", HEADER + "x;Create;03/03/2024 10:00\n")  # does not match the pattern
    first = feeds.run(feed["feed_id"])
    assert (first["files_appended"], first["events_added"], first["files_still_being_written"]) == (1, 1, 1)
    assert first["files"][0]["duplicate_events_skipped"] == 1
    # Later the second file has settled; the first is not appended again, even renamed.
    later = time.time() + 120
    (folder / "orders-0301.csv").rename(folder / "orders-0301-copy.csv")
    second = feeds.run(feed["feed_id"], now=later)
    assert (second["files_appended"], second["events_added"], second["files_already_appended"]) == (1, 1, 1)
    assert workspace.summary(log)["events"] == 4
    # A broken file is recorded as failed and not retried with the same content.
    _drop(folder, "orders-0303.csv", "Wrong;Header\n1;2\n")
    third = feeds.run(feed["feed_id"], now=later)
    assert third["files_failed"] == 1 and "missing required columns" in third["files"][0]["error"]
    assert feeds.run(feed["feed_id"], now=later)["files_failed"] == 0
    listed = feeds.feeds(log)["feeds"][0]
    assert (listed["files_appended"], listed["files_failed"]) == (2, 1)
    assert [f["outcome"] for f in listed["recent_files"]].count("appended") == 2
    assert (folder / "orders-0303.csv").exists(), "files in the folder are never removed"


def test_preview_compares_content_and_leaves_feed_history_untouched(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    feed_id = feeds.create(log, {"folder": str(folder), "pattern": "orders-*.csv",
                                 "confirm_folder_reads": True})["feed_id"]
    earlier = _drop(folder, "orders-earlier.csv", HEADER + "o2;Create;04/03/2024 10:00\n")
    _drop(folder, "orders-failed.csv", "Wrong;Header\n1;2\n")
    run = feeds.run(feed_id)
    assert (run["files_appended"], run["files_failed"]) == (1, 1)
    earlier.rename(folder / "orders-renamed.csv")
    row = HEADER + "o3;Create;05/03/2024 10:00\n"
    _drop(folder, "orders-new.csv", row, age=300)
    _drop(folder, "orders-copy.csv", row, age=200)
    _drop(folder, "orders-current.csv", row, age=2)
    _drop(folder, "unmatched.csv", row)
    (folder / "orders-dir.csv").mkdir()
    history = feeds.feeds(log)
    summary = workspace.summary(log)

    def forbid(*args, **kwargs):
        pytest.fail("the preview must not stage or append content")

    monkeypatch.setattr(feeds, "_snapshot", forbid)
    monkeypatch.setattr(workspace, "append_log", forbid)
    review = feeds.preview(feed_id)
    assert {file["name"]: file["state"] for file in review["files"]} == {
        "orders-renamed.csv": "already_appended",
        "orders-failed.csv": "previously_failed",
        "orders-new.csv": "new_content",
        "orders-copy.csv": "repeated_content",
        "orders-current.csv": "settling",
    }
    assert review["counts"]["new_content"] == 1 and not review["more_files"]
    assert all(("sha256" in file) == (file["state"] == "new_content") for file in review["files"])
    assert feeds.feeds(log) == history and workspace.summary(log) == summary


def test_append_one_pins_the_selected_name_and_content(tmp_path):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    feed_id = feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})["feed_id"]
    first = HEADER + "o2;Create;04/03/2024 10:00\n"
    second = HEADER + "o3;Ship;05/03/2024 10:00\n"
    _drop(folder, "other.csv", first, age=600)
    chosen = _drop(folder, "chosen.csv", second, age=300)
    reviewed = next(row for row in feeds.preview(feed_id)["files"] if row["name"] == chosen.name)
    spec = {"name": reviewed["name"], "sha256": reviewed["sha256"]}
    _drop(folder, chosen.name, second.replace("Ship", "Edit"), age=300)
    result = feeds.append_one(feed_id, spec)
    assert result["selected_changed"] and result["files"] == []
    assert workspace.summary(log)["events"] == 2
    assert feeds.feeds(log)["feeds"][0]["recent_files"] == []

    _drop(folder, chosen.name, second, age=300)
    result = feeds.append_one(feed_id, spec)
    assert result["files_appended"] == 1 and not result["selected_changed"]
    assert result["files"][0]["name"] == chosen.name
    assert workspace.summary(log)["events"] == 3
    assert feeds.append_one(feed_id, spec)["files_already_appended"] == 1
    assert next(row for row in feeds.preview(feed_id)["files"] if row["name"] == "other.csv")["state"] == "new_content"
    assert workspace.summary(log)["events"] == 3


def test_preview_bounds_hashing_bytes_and_the_number_of_files(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    feed = feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})
    _drop(folder, "first.csv", "x" * 10, age=500)
    _drop(folder, "over-budget.csv", "y" * 10, age=400)
    _drop(folder, "too-large.csv", "z" * 20, age=300)
    _drop(folder, "outside-page.csv", "new", age=200)
    monkeypatch.setattr(feeds, "MAX_PREVIEW_FILES", 3)
    monkeypatch.setattr(feeds, "MAX_PREVIEW_BYTES", 15)
    monkeypatch.setattr(workspace, "MAX_IMPORT_BYTES", 15)
    result = feeds.preview(feed["feed_id"])
    assert result["more_files"] is True
    assert [file["state"] for file in result["files"]] == ["new_content", "preview_limit", "too_large"]


def test_feed_needs_a_csv_mapping_and_scheduler_runs_it(tmp_path):
    log = _log()
    folder = tmp_path / "exports"
    folder.mkdir()
    feed = feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})
    _drop(folder, "a.csv", HEADER + "o3;Create;05/03/2024 10:00\n")
    assert feeds.run_scheduled(feed["feed_id"]) == ("succeeded:1_files_appended", 0.0)
    assert feeds.run_scheduled(feed["feed_id"]) == ("succeeded:0_files_appended", 0.0)
    schedule = scheduler.create("event_log_folder_feed", feed["feed_id"], interval_minutes=60, confirm_scheduled_runs=True)
    assert schedule["kind"] == "event_log_folder_feed"
    with pytest.raises(scheduler.ScheduleError, match="unknown event log folder feed"):
        scheduler.create("event_log_folder_feed", "feed_" + "0" * 16, interval_minutes=60, confirm_scheduled_runs=True)
    # Deleting the log deletes its feeds, their records and the schedule.
    workspace.delete_log(log)
    assert scheduler.status()["schedules"] == []
    with pytest.raises(workspace.EventLogWorkspaceError, match="unknown feed"):
        feeds.get(feed["feed_id"])


def test_ocel_logs_have_no_mapping_to_reuse(tmp_path):
    doc = ('{"objectTypes":[{"name":"order","attributes":[]}],"eventTypes":[{"name":"A","attributes":[]}],'
           '"objects":[{"id":"o1","type":"order","attributes":[]}],"events":[{"id":"e1","type":"A",'
           '"time":"2024-01-01T00:00:00Z","attributes":[],"relationships":[{"objectId":"o1","qualifier":""}]}]}')
    log = workspace.import_log(BytesIO(doc.encode()), name="o", source_format="ocel")["log_id"]
    folder = tmp_path / "exports"
    folder.mkdir()
    with pytest.raises(workspace.EventLogWorkspaceError, match="no CSV import"):
        feeds.create(log, {"folder": str(folder), "confirm_folder_reads": True})


def test_routes(tmp_path):
    import json
    import subprocess
    import sys
    folder = tmp_path / "exports"
    folder.mkdir()
    _drop(folder, "a.csv", "case,activity,time\nc9,A,2024-03-05T08:00:00Z\n")
    code = r'''
import json, sys
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token()}
    store = {**owner, "X-OWG-Local-Storage-Confirmed": "true"}
    log_id = client.post("/v1/event-logs/import?format=csv&name=t&case_column=case&activity_column=activity&timestamp_column=time",
                         content=b"case,activity,time\nc1,A,2024-03-04T08:00:00Z\n", headers=store).json()["log_id"]
    spec = {"folder": sys.argv[1], "confirm_folder_reads": True}
    assert client.post(f"/v1/event-logs/{log_id}/feeds", json=spec, headers=bearer).status_code == 403
    r = client.post(f"/v1/event-logs/{log_id}/feeds", json=spec, headers=owner)
    assert r.status_code == 200, r.text
    feed_id = r.json()["feed_id"]
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=owner).status_code == 403
    preview_url = f"/v1/event-logs/feeds/{feed_id}/preview"
    assert client.get(preview_url, headers=bearer).status_code == 403
    preview = client.get(preview_url, headers=owner)
    assert preview.status_code == 200 and preview.headers["cache-control"] == "no-store"
    assert [(item["name"], item["state"]) for item in preview.json()["files"]] == [("a.csv", "new_content")]
    candidate = preview.json()["files"][0]
    single = f"/v1/event-logs/feeds/{feed_id}/append-file"
    selected = {"name": candidate["name"], "sha256": candidate["sha256"]}
    assert client.post(single, json=selected, headers=owner).status_code == 403
    assert client.post(single, json=selected, headers={**bearer, "X-OWG-Local-Storage-Confirmed": "true"}).status_code == 403
    assert client.post(single, json={**selected, "name": "../a.csv"}, headers=store).status_code == 422
    moved = client.post(single, json={**selected, "name": "missing.csv"}, headers=store)
    assert moved.status_code == 200 and moved.json()["selected_changed"]


    assert client.post(f"/v1/event-logs/feeds/{feed_id}/retry-failed", headers=bearer).status_code == 403
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/retry-failed", headers=owner).status_code == 403
    assert client.get(f"/v1/event-logs/feeds/{feed_id}/failures", headers=bearer).status_code == 403
    failure_page = client.get(f"/v1/event-logs/feeds/{feed_id}/failures", headers=owner)
    assert failure_page.status_code == 200 and failure_page.headers['cache-control'] == 'no-store'
    assert failure_page.json()['failures'] == []
    assert client.get(f"/v1/event-logs/feeds/{feed_id}/failures?after=bad", headers=owner).status_code == 422
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/retry-failed/" + '0'*64, headers=owner).status_code == 403
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/retry-failed/bad", headers=store).status_code == 422
    r = client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=store)
    assert r.status_code == 200 and r.json()["events_added"] == 1, r.text
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/retry-failed", headers=store).json()["files_appended"] == 0
    assert client.get(f"/v1/event-logs/{log_id}/feeds", headers=owner).json()["feeds"][0]["files_appended"] == 1
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/delete", headers=owner).status_code == 200
    assert client.post(f"/v1/event-logs/feeds/{feed_id}/run", headers=store).status_code == 422
print("ok")
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "srv"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    done = subprocess.run([sys.executable, "-c", code, str(folder)], cwd=Path(__file__).resolve().parents[1], env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and done.stdout.strip().endswith("ok"), f"stdout={done.stdout}\nstderr={done.stderr}"


def test_snapshot_hash_matches_the_bytes_actually_appended(tmp_path, monkeypatch):
    import hashlib
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    original = (HEADER + 'o2;Create;03/03/2024 10:00\n').encode()
    replacement = (HEADER + 'o3;Create;04/03/2024 10:00\n').encode()
    path = _drop(folder, 'a.csv', original.decode())
    # write_text() normalizes newlines on Windows; hash the actual bytes
    # the importer reads, not the pre-write Unix-style fixture string.
    original = path.read_bytes()
    append = workspace.append_log
    def replace_before_append(*args, **kwargs):
        path.write_bytes(replacement)
        return append(*args, **kwargs)
    monkeypatch.setattr(workspace, 'append_log', replace_before_append)
    result = feeds.run(feed['feed_id'])
    assert result['events_added'] == 1
    recorded = feeds.feeds(log)['feeds'][0]['recent_files'][0]
    assert recorded['sha256'] == hashlib.sha256(original).hexdigest()
    monkeypatch.setattr(workspace, 'append_log', append)
    old = time.time() - 600
    os.utime(path, (old, old))
    assert feeds.run(feed['feed_id'])['events_added'] == 1
    assert workspace.summary(log)['events'] == 4


def test_concurrent_runs_record_and_append_a_content_hash_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Barrier
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    snapshot = feeds._snapshot
    barrier = Barrier(2)
    @contextmanager
    def simultaneous(*args):
        with snapshot(*args) as captured:
            barrier.wait(timeout=20)
            yield captured
    monkeypatch.setattr(feeds, '_snapshot', simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: feeds.run(feed['feed_id']), range(2)))
    assert sorted(r['files_appended'] for r in runs) == [0, 1]
    assert sum(r['files_already_appended'] for r in runs) == 1
    assert feeds.feeds(log)['feeds'][0]['files_appended'] == 1
    assert workspace.summary(log)['events'] == 3


def test_committed_append_is_not_retried_if_quality_report_fails(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    def fail_report(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('report unavailable')
    monkeypatch.setattr(workspace, '_record_quality', fail_report)
    first = feeds.run(feed['feed_id'])
    assert (first['files_appended'], first['files_failed'], first['events_added']) == (1, 0, 1)
    assert 'quality report' in first['files'][0]['warning']
    assert feeds.run(feed['feed_id'])['files_already_appended'] == 1
    assert feeds.feeds(log)['feeds'][0]['files_appended'] == 1
    assert workspace.summary(log)['events'] == 3


def test_snapshot_refuses_replaced_file(tmp_path):
    path = _drop(tmp_path, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    old = path.lstat()
    replacement = _drop(tmp_path, 'replacement.csv', HEADER + 'o3;Create;04/03/2024 10:00\n')
    replacement.replace(path)
    with pytest.raises(feeds._FileChanged):
        with feeds._snapshot(path, old):
            pytest.fail('a replacement must not be read under the old metadata')


def test_snapshot_defers_file_changed_while_reading(tmp_path, monkeypatch):
    path = _drop(tmp_path, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    expected = path.lstat()
    sha256 = feeds.hashlib.sha256
    class ChangingDigest:
        def __init__(self):
            self.inner = sha256()
        def update(self, chunk):
            self.inner.update(chunk)
            path.write_text(HEADER + 'o3;Ship;04/03/2024 10:00\nextra', encoding='utf-8')
        def hexdigest(self):
            return self.inner.hexdigest()
    monkeypatch.setattr(feeds.hashlib, 'sha256', ChangingDigest)
    with pytest.raises(feeds._FileChanged):
        with feeds._snapshot(path, expected):
            pytest.fail('a changing file must be deferred')


def test_unexpected_post_commit_failure_still_prevents_replay(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    def crash(*args, **kwargs):
        raise RuntimeError('simulated post-commit failure')
    monkeypatch.setattr(workspace, '_record_quality', crash)
    with pytest.raises(RuntimeError, match='post-commit'):
        feeds.run(feed['feed_id'])
    assert feeds.run(feed['feed_id'])['files_already_appended'] == 1
    assert feeds.feeds(log)['feeds'][0]['files_appended'] == 1
    assert workspace.summary(log)['events'] == 3


def test_scheduled_run_reports_missing_quality_report(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    def fail_report(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('report unavailable')
    monkeypatch.setattr(workspace, '_record_quality', fail_report)
    assert feeds.run_scheduled(feed['feed_id']) == ('failed:quality_report_unavailable', 0.0)
    assert feeds.feeds(log)['feeds'][0]['files_appended'] == 1


def test_receipt_and_events_roll_back_together(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    append = workspace.append_log
    def interrupted(*args, **kwargs):
        record = kwargs['_transaction_hook']
        def stop_after_receipt(conn, log_id):
            record(conn, log_id)
            raise workspace.EventLogWorkspaceError('synthetic transaction failure')
        kwargs['_transaction_hook'] = stop_after_receipt
        return append(*args, **kwargs)
    monkeypatch.setattr(workspace, 'append_log', interrupted)
    result = feeds.run(feed['feed_id'])
    assert (result['files_appended'], result['files_failed']) == (0, 1)
    listed = feeds.feeds(log)['feeds'][0]
    assert (listed['files_appended'], listed['files_failed']) == (0, 1)
    assert workspace.summary(log)['events'] == 2


def test_oversized_file_is_reported_without_a_false_hash_receipt(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    monkeypatch.setattr(workspace, 'MAX_IMPORT_BYTES', 8)
    for _ in range(2):
        result = feeds.run(feed['feed_id'])
        assert result['files_failed'] == 1
        assert 'larger than the supported limit' in result['files'][0]['error']
    assert feeds.feeds(log)['feeds'][0]['recent_files'] == []
    assert workspace.summary(log)['events'] == 2


def test_feed_deleted_during_snapshot_is_not_recreated(tmp_path, monkeypatch):
    from contextlib import contextmanager
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'a.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    snapshot = feeds._snapshot
    @contextmanager
    def deleted_before_append(*args):
        with snapshot(*args) as captured:
            feeds.delete(feed['feed_id'])
            yield captured
    monkeypatch.setattr(feeds, '_snapshot', deleted_before_append)
    assert feeds.run(feed['feed_id'])['files_failed'] == 1
    assert feeds.feeds(log)['feeds'] == []
    assert workspace.summary(log)['events'] == 2


def test_explicit_retry_processes_only_previously_failed_content(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'failed.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    original = workspace.append_log
    def unavailable(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('temporary ingestion failure')
    monkeypatch.setattr(workspace, 'append_log', unavailable)
    assert feeds.run(feed['feed_id'])['files_failed'] == 1
    skipped = feeds.run(feed['feed_id'])
    assert (skipped['files_already_appended'], skipped['files_previously_failed']) == (0, 1)
    assert feeds.run(feed['feed_id'], retry_failed=True)['files_failed'] == 1
    listed = feeds.feeds(log)['feeds'][0]
    assert (listed['files_failed'], len(listed['recent_files'])) == (1, 1)
    monkeypatch.setattr(workspace, 'append_log', original)
    _drop(folder, 'new.csv', HEADER + 'o3;Create;04/03/2024 10:00\n')
    retried = feeds.run(feed['feed_id'], retry_failed=True)
    assert (retried['files_appended'], retried['events_added'], retried['new_files_left_for_normal_run']) == (1, 1, 1)
    assert retried['files'][0]['name'] == 'failed.csv'
    listed = feeds.feeds(log)['feeds'][0]
    assert (listed['files_appended'], listed['files_failed'], len(listed['recent_files'])) == (1, 0, 1)
    assert feeds.run(feed['feed_id'], retry_failed=True)['files_appended'] == 0
    assert feeds.run(feed['feed_id'])['events_added'] == 1
    assert workspace.summary(log)['events'] == 4


def test_retry_failure_rolls_back_replaced_receipt_and_keeps_error(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'failed.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    append = workspace.append_log
    def fail_before_append(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('initial failure')
    monkeypatch.setattr(workspace, 'append_log', fail_before_append)
    assert feeds.run(feed['feed_id'])['files_failed'] == 1
    _drop(folder, 'copy.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    attempts = []
    def interrupted(*args, **kwargs):
        attempts.append(1)
        record = kwargs['_transaction_hook']
        def fail_after_record(conn, log_id):
            record(conn, log_id)
            raise workspace.EventLogWorkspaceError('retry transaction failure')
        kwargs['_transaction_hook'] = fail_after_record
        return append(*args, **kwargs)
    monkeypatch.setattr(workspace, 'append_log', interrupted)
    retried = feeds.run(feed['feed_id'], retry_failed=True)
    assert retried['files_failed'] == 1
    assert retried['duplicate_failed_files_skipped'] == 1
    assert retried['files_already_appended'] == 0
    assert len(attempts) == 1
    listed = feeds.feeds(log)['feeds'][0]
    assert (listed['files_appended'], listed['files_failed'], len(listed['recent_files'])) == (0, 1, 1)
    assert listed['recent_files'][0]['error'] == 'retry transaction failure'
    assert workspace.summary(log)['events'] == 2


def test_retry_committed_before_quality_failure_is_not_replayed(tmp_path, monkeypatch):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'failed.csv', HEADER + 'o2;Create;03/03/2024 10:00\n')
    append = workspace.append_log
    def unavailable(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('temporary failure')
    monkeypatch.setattr(workspace, 'append_log', unavailable)
    assert feeds.run(feed['feed_id'])['files_failed'] == 1
    monkeypatch.setattr(workspace, 'append_log', append)
    def fail_quality(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('quality report failure')
    monkeypatch.setattr(workspace, '_record_quality', fail_quality)
    result = feeds.run(feed['feed_id'], retry_failed=True)
    assert (result['files_appended'], result['files_failed']) == (1, 0)
    assert 'quality report' in result['files'][0]['warning']
    assert feeds.run(feed['feed_id'], retry_failed=True)['files_appended'] == 0
    listed = feeds.feeds(log)['feeds'][0]
    assert (listed['files_appended'], listed['files_failed'], len(listed['recent_files'])) == (1, 0, 1)
    assert workspace.summary(log)['events'] == 3


def test_failure_pages_are_bounded_distinct_and_scoped(tmp_path):
    import json
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            for i in range(105):
                conn.execute('INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)',
                    [log, feed['feed_id'], f'{i}.csv', f'{i:064x}', 12, 'failed', json.dumps({'error': 'bad'})])
            # An earlier failure with a later successful receipt is not outstanding.
            conn.execute('INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)',
                [log, feed['feed_id'], 'done.csv', f'{0:064x}', 12, 'appended', json.dumps({'added_events': 1})])
        finally:
            conn.close()
    first = feeds.failures(feed['feed_id'])
    second = feeds.failures(feed['feed_id'], after=first['next_after'])
    third = feeds.failures(feed['feed_id'], after=second['next_after'])
    assert [len(p['failures']) for p in (first, second, third)] == [50, 50, 4]
    all_hashes = [f['sha256'] for p in (first, second, third) for f in p['failures']]
    assert all_hashes == sorted(set(all_hashes)) and f'{0:064x}' not in all_hashes
    assert third['next_after'] is None
    with pytest.raises(workspace.EventLogWorkspaceError, match='cursor'):
        feeds.failures(feed['feed_id'], after="' OR 1=1 --")
    with pytest.raises(workspace.EventLogWorkspaceError, match='unknown feed'):
        feeds.failures('feed_' + '0'*16)


def test_retry_selected_hash_leaves_other_failures_and_new_files(tmp_path, monkeypatch):
    import hashlib
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    first = HEADER + 'o2;Create;03/03/2024 10:00\n'
    second = HEADER + 'o3;Create;04/03/2024 10:00\n'
    _drop(folder, 'first.csv', first)
    _drop(folder, 'second.csv', second)
    append = workspace.append_log
    def unavailable(*args, **kwargs):
        raise workspace.EventLogWorkspaceError('temporary failure')
    monkeypatch.setattr(workspace, 'append_log', unavailable)
    assert feeds.run(feed['feed_id'])['files_failed'] == 2
    monkeypatch.setattr(workspace, 'append_log', append)
    _drop(folder, 'new.csv', HEADER + 'o4;Create;05/03/2024 10:00\n')
    (folder / 'second.csv').rename(folder / 'renamed.csv')
    wanted = hashlib.sha256(second.encode()).hexdigest()
    result = feeds.run(feed['feed_id'], retry_failed=True, retry_hash=wanted)
    assert (result['files_appended'], result['events_added']) == (1, 1)
    assert result['files'][0]['name'] == 'renamed.csv'
    remaining = feeds.failures(feed['feed_id'])['failures']
    assert [item['sha256'] for item in remaining] == [hashlib.sha256(first.encode()).hexdigest()]
    assert workspace.summary(log)['events'] == 3
    assert feeds.run(feed['feed_id'], retry_failed=True, retry_hash=wanted)['files_appended'] == 0
    with pytest.raises(workspace.EventLogWorkspaceError, match='SHA-256'):
        feeds.run(feed['feed_id'], retry_failed=True, retry_hash='bad')
    with pytest.raises(workspace.EventLogWorkspaceError, match='SHA-256'):
        feeds.run(feed['feed_id'], retry_hash=wanted)


def test_selected_retry_reaches_failure_beyond_bulk_run_limit(tmp_path):
    import hashlib
    import json
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    hashes = []
    with workspace._LOCK:
        conn = workspace._connect()
        try:
            for i in range(feeds.MAX_FILES_PER_RUN + 5):
                content = HEADER + f'o{i+10};Create;03/03/2024 10:00\n'
                name = f'{i:02}.csv'
                _drop(folder, name, content, age=1000-i)
                sha = hashlib.sha256(content.encode()).hexdigest()
                hashes.append(sha)
                conn.execute('INSERT INTO event_log_feed_files VALUES (?, ?, ?, ?, ?, now(), ?, ?)',
                    [log, feed['feed_id'], name, sha, len(content), 'failed', json.dumps({'error':'old failure'})])
        finally:
            conn.close()
    result = feeds.run(feed['feed_id'], retry_failed=True, retry_hash=hashes[-1])
    assert result['files_appended'] == 1 and result['files'][0]['name'] == '24.csv'
    assert workspace.summary(log)['events'] == 3
    assert len(feeds.failures(feed['feed_id'])['failures']) == 24



def test_feed_preserves_literal_folder_and_pattern_whitespace(tmp_path):
    log = _log()
    plain = tmp_path / 'exports'
    spaced = tmp_path / 'exports '
    plain.mkdir()
    try:
        spaced.mkdir()
    except OSError:
        pytest.skip('filesystem does not allow a trailing space in a folder name')
    if plain.samefile(spaced):
        pytest.skip('filesystem does not distinguish trailing spaces in folder names')
    _drop(plain, ' 订单-1.csv', HEADER + 'wrong;Create;03/03/2024 10:00\n')
    _drop(spaced, '订单-1.csv', HEADER + 'also-wrong;Create;03/03/2024 10:00\n')
    _drop(spaced, ' 订单-1.csv', HEADER + 'wanted;Create;03/03/2024 10:00\n')
    feed = feeds.create(log, {'folder': str(spaced), 'pattern': ' 订单-*.csv', 'confirm_folder_reads': True})
    assert feed['folder'] == str(spaced.resolve())
    assert feed['pattern'] == ' 订单-*.csv'
    result = feeds.run(feed['feed_id'])
    assert result['events_added'] == 1 and [f['name'] for f in result['files']] == [' 订单-1.csv']
    case_ids = workspace._query(log, None, lambda conn: {'ids': [r[0] for r in conn.execute('SELECT DISTINCT case_id FROM ce').fetchall()]})['ids']
    assert 'wanted' in case_ids and 'wrong' not in case_ids and 'also-wrong' not in case_ids


@pytest.mark.parametrize('pattern', ['../*.csv', '..\\*.csv', 'a/b.csv', 'a\\b.csv', 'a\n.csv', 'a\x7f.csv', 'a\ud800.csv', 'x'*81+'.csv', 0, False, [], {}])
def test_feed_pattern_rejects_path_separators_controls_and_oversize(tmp_path, pattern):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    with pytest.raises(workspace.EventLogWorkspaceError, match='pattern'):
        feeds.create(log, {'folder': str(folder), 'pattern': pattern, 'confirm_folder_reads': True})


def test_unresolvable_home_is_a_readable_validation_error(monkeypatch):
    def fail_expand(self):
        raise RuntimeError('home unavailable')
    monkeypatch.setattr(feeds.Path, 'expanduser', fail_expand)
    with pytest.raises(workspace.EventLogWorkspaceError, match='absolute path'):
        feeds._folder('~missing/exports')


def test_invalid_surrogate_in_folder_is_rejected():
    with pytest.raises(workspace.EventLogWorkspaceError, match='absolute path'):
        feeds._folder('/tmp/\ud800')


def test_scheduled_feed_reports_unsettled_content_as_partial_then_recovers(tmp_path):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    path = _drop(folder, 'pending.csv', HEADER + 'o2;Create;03/03/2024 10:00\n', age=5)
    assert feeds.run_scheduled(feed['feed_id']) == ('partial:files_not_ready', 0.0)
    assert workspace.summary(log)['events'] == 2
    old = time.time() - 600
    os.utime(path, (old, old))
    assert feeds.run_scheduled(feed['feed_id']) == ('succeeded:1_files_appended', 0.0)
    assert workspace.summary(log)['events'] == 3


def test_scheduler_keeps_partial_feed_enabled_and_records_partial_status(tmp_path):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    _drop(folder, 'pending.csv', HEADER + 'o2;Create;03/03/2024 10:00\n', age=5)
    job = scheduler.create('event_log_folder_feed', feed['feed_id'], interval_minutes=60, confirm_scheduled_runs=True)
    stored = scheduler._read()
    stored['schedules'][job['schedule_id']]['consecutive_failures'] = 4
    scheduler._write(stored)
    due = time.time() + 7200
    assert scheduler.run_due(now=due)[0]['outcome'] == 'partial:files_not_ready'
    saved = scheduler.status()['schedules'][0]
    assert saved['schedule_id'] == job['schedule_id']
    assert saved['last_result'] == 'partial:files_not_ready'
    assert saved['enabled'] is True and saved['consecutive_failures'] == 0


def test_scheduled_feed_reports_unchecked_backlog_at_batch_limit(tmp_path):
    log = _log()
    folder = tmp_path / 'exports'
    folder.mkdir()
    feed = feeds.create(log, {'folder': str(folder), 'confirm_folder_reads': True})
    for i in range(feeds.MAX_FILES_PER_RUN + 1):
        _drop(folder, f'{i:02}.csv', HEADER + f'o{i+10};Create;03/03/2024 10:00\n', age=1000-i)
    assert feeds.run_scheduled(feed['feed_id']) == ('partial:more_files_pending', 0.0)
    assert workspace.summary(log)['events'] == 22
    assert feeds.run_scheduled(feed['feed_id']) == ('succeeded:1_files_appended', 0.0)
    assert workspace.summary(log)['events'] == 23


def test_scheduled_feed_discloses_both_partial_conditions(monkeypatch):
    monkeypatch.setattr(feeds, 'run', lambda _feed_id: {
        'files_failed': 0, 'files_unreadable': 0, 'files_previously_failed': 0,
        'more_files_waiting': True, 'files_still_being_written': 1,
        'files_appended': 20, 'files': [],
    })
    assert feeds.run_scheduled('feed_' + '0'*16) == ('partial:more_files_pending_and_not_ready', 0.0)
