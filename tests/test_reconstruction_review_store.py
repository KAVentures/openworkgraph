"""Regression coverage for explicit, local reconstruction review persistence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os

import pytest

from server import reconstruction_review_store as store


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_path", lambda: tmp_path / "review.json")
    monkeypatch.setattr(store, "_retention_allowed", lambda: True)
    return tmp_path / "review.json"


def _envelope():
    return {"evidence_sha256": "a" * 64, "annotations": [
        {"window": "one", "event_id": "event-1", "true_resource_pairs": []}
    ]}


def test_explicit_save_restore_and_delete(local_store):
    grant = {"mode": "all_saved", "expires_at": "fixed"}
    assert store.read_saved_reconstruction_review(grant, 7, "a" * 64) is None
    saved = store.save_reconstruction_review(grant, 7, _envelope())
    assert saved["agent_access_granted"] is False
    assert saved["confirmed_cases"] is False
    restored = store.read_saved_reconstruction_review(grant, 7, "a" * 64)
    assert restored["annotations"] == _envelope()["annotations"]
    assert restored["reviewer_declared_not_independently_verified"] is True
    if os.name == "posix":
        assert local_store.stat().st_mode & 0o077 == 0
    store.clear_saved_reconstruction_review()
    assert not local_store.exists()


@pytest.mark.parametrize("grant,generation,digest", [
    ({"mode": "none"}, 7, "a" * 64),
    ({"mode": "all_saved", "expires_at": "changed"}, 7, "a" * 64),
    ({"mode": "all_saved", "expires_at": "fixed"}, 8, "a" * 64),
    ({"mode": "all_saved", "expires_at": "fixed"}, 7, "b" * 64),
])
def test_grant_generation_and_evidence_changes_fail_closed(local_store, grant, generation, digest):
    original = {"mode": "all_saved", "expires_at": "fixed"}
    store.save_reconstruction_review(original, 7, _envelope())
    assert store.read_saved_reconstruction_review(grant, generation, digest) is None
    assert not local_store.exists()


def test_expired_review_is_purged(local_store):
    grant = {"mode": "all_saved"}
    store.save_reconstruction_review(grant, 1, _envelope())
    value = json.loads(local_store.read_text())
    value["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    local_store.write_text(json.dumps(value))
    assert store.read_saved_reconstruction_review(grant, 1, "a" * 64) is None
    assert not local_store.exists()


def test_no_persistence_without_retention(local_store, monkeypatch):
    monkeypatch.setattr(store, "_retention_allowed", lambda: False)
    with pytest.raises(PermissionError):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())


def test_bounded_payload_and_invalid_digest(local_store):
    grant = {"mode": "all_saved"}
    with pytest.raises(ValueError):
        store.save_reconstruction_review(grant, 1, {"evidence_sha256": "bad", "annotations": [1]})
    huge = {"evidence_sha256": "a" * 64, "annotations": [
        {"event_id": "x" * 200000}
    ]}
    with pytest.raises(ValueError, match="storage limit"):
        store.save_reconstruction_review(grant, 1, huge)
    assert not local_store.exists()


def test_symlink_is_not_followed(local_store, tmp_path):
    target = tmp_path / "unrelated"
    target.write_text("do not modify")
    local_store.symlink_to(target)
    with pytest.raises(OSError):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())
    assert target.read_text() == "do not modify"


def test_background_retention_cleanup_purges_review_labels(local_store, monkeypatch):
    from server import history_routes

    grant = {"mode": "all_saved"}
    store.save_reconstruction_review(grant, 1, _envelope())
    assert local_store.exists()
    calls = []

    class OnePass:
        def __init__(self):
            self.count = 0

        def wait(self, seconds):
            self.count += 1
            assert seconds == 3600
            return self.count > 1

    monkeypatch.setattr(history_routes, "_retention_stop", OnePass())
    monkeypatch.setattr(history_routes, "cleanup_expired_history", lambda startup: calls.append(startup))
    history_routes._retention_loop()
    assert calls == [False]
    assert not local_store.exists()


def test_shutdown_retention_cleanup_purges_review_labels(local_store, monkeypatch):
    from server import history_routes

    store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())
    monkeypatch.setattr(history_routes, "_retention_thread", None)
    monkeypatch.setattr(history_routes, "cleanup_expired_history", lambda startup: None)
    history_routes._history_shutdown()
    assert not local_store.exists()


def test_saved_review_read_rejects_oversized_file_after_open(local_store, monkeypatch):
    """A path size check must not authorize an unbounded later read."""
    grant = {"mode": "all_saved"}
    store.save_reconstruction_review(grant, 1, _envelope())
    original = os.fstat

    def report_small_size(fd):
        info = original(fd)
        # A file can grow after its metadata is observed; the reader must
        # independently enforce the byte limit on the opened descriptor.
        class SizeView:
            st_mode = info.st_mode
            st_size = 1
            st_ino = info.st_ino
        return SizeView()

    local_store.write_bytes(b" " * (store._MAX_BYTES + 1))
    monkeypatch.setattr(store.os, "fstat", report_small_size)
    assert store.read_saved_reconstruction_review(grant, 1, "a" * 64) is None
    assert not local_store.exists()


def test_saved_review_read_rejects_symlink_without_reading_target(local_store, tmp_path):
    target = tmp_path / "private-other-file"
    target.write_text('{"secret":"unrelated"}')
    local_store.symlink_to(target)
    assert store.read_saved_reconstruction_review({"mode": "all_saved"}, 1, "a" * 64) is None
    assert target.read_text() == '{"secret":"unrelated"}'
