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


@pytest.mark.parametrize("bad_envelope", [None, [], "labels", 7, True])
def test_non_object_review_envelope_fails_closed(local_store, bad_envelope):
    with pytest.raises(ValueError, match="review envelope must be an object"):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, bad_envelope)
    assert not local_store.exists()


def test_non_json_review_annotation_fails_closed(local_store):
    envelope = _envelope()
    envelope["annotations"][0]["unexpected"] = {1, 2}
    with pytest.raises(ValueError, match="invalid review annotation payload"):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, envelope)
    assert not local_store.exists()


def test_deeply_nested_saved_review_fails_closed(local_store):
    # A bounded file can still exceed Python's JSON recursion limit. Treat
    # malformed local state as absent rather than crashing the review API.
    local_store.write_text("[" * 1200 + "0" + "]" * 1200)
    if os.name == "posix":
        local_store.chmod(0o600)
    assert store.read_saved_reconstruction_review(
        {"mode": "all_saved"}, 1, "a" * 64,
    ) is None
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


def test_retention_revoked_between_validation_and_atomic_write(local_store, monkeypatch):
    """A revocation during serialization must not persist reviewer labels."""
    calls = iter([True, False])
    monkeypatch.setattr(store, "_retention_allowed", lambda: next(calls))
    with pytest.raises(PermissionError, match="revoked"):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())
    assert not local_store.exists()


def test_concurrent_delete_waits_for_atomic_save_and_wins(local_store, monkeypatch):
    """A local deletion racing with an atomic save must not resurrect labels.

    This covers in-process serialization, not cross-process revocation.
    """
    import threading

    grant = {"mode": "all_saved"}
    entered_replace = threading.Event()
    allow_replace = threading.Event()
    original_replace = store.os.replace
    errors = []

    def delayed_replace(src, dst):
        entered_replace.set()
        if not allow_replace.wait(timeout=5):
            raise TimeoutError("test did not release atomic replace")
        return original_replace(src, dst)

    monkeypatch.setattr(store.os, "replace", delayed_replace)

    def save():
        try:
            store.save_reconstruction_review(grant, 1, _envelope())
        except Exception as exc:
            errors.append(exc)

    def delete():
        try:
            store.clear_saved_reconstruction_review()
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(target=save)
    writer.start()
    assert entered_replace.wait(timeout=5)
    deleter = threading.Thread(target=delete)
    deleter.start()
    try:
        deleter.join(timeout=0.05)
        assert deleter.is_alive(), "delete must wait until atomic save releases lock"
    finally:
        allow_replace.set()
        writer.join(timeout=5)
        deleter.join(timeout=5)
    assert not writer.is_alive() and not deleter.is_alive()
    assert not errors
    assert not local_store.exists()


def test_retention_revoked_during_saved_review_read_returns_no_labels(local_store, monkeypatch):
    """Never return previously retained labels after a mid-read revocation."""
    grant = {"mode": "all_saved"}
    store.save_reconstruction_review(grant, 1, _envelope())
    calls = iter([True, False])
    monkeypatch.setattr(store, "_retention_allowed", lambda: next(calls))
    assert store.read_saved_reconstruction_review(grant, 1, "a" * 64) is None
    assert not local_store.exists()


def test_retention_revoked_during_atomic_replace_removes_saved_labels(local_store, monkeypatch):
    """A mid-write revocation must not acknowledge or retain a snapshot."""
    grant = {"mode": "all_saved"}
    allowed = {"value": True}
    monkeypatch.setattr(store, "_retention_allowed", lambda: allowed["value"])
    original_replace = store.os.replace

    def revoke_during_replace(src, dst):
        original_replace(src, dst)
        allowed["value"] = False

    monkeypatch.setattr(store.os, "replace", revoke_during_replace)
    with pytest.raises(PermissionError, match="revoked during persistence"):
        store.save_reconstruction_review(grant, 1, _envelope())
    assert not local_store.exists()


@pytest.mark.parametrize("bad_expiry", [None, [], {}, 999999999999999999999])
def test_corrupted_saved_review_expiry_fails_closed(local_store, bad_expiry):
    """Untrusted disk metadata must not crash the authenticated read endpoint."""
    grant = {"mode": "all_saved"}
    store.save_reconstruction_review(grant, 1, _envelope())
    record = json.loads(local_store.read_text())
    record["expires_at"] = bad_expiry
    local_store.write_text(json.dumps(record))
    assert store.read_saved_reconstruction_review(grant, 1, "a" * 64) is None
    assert not local_store.exists()


def test_cross_process_delete_waits_for_snapshot_lock(local_store, tmp_path, monkeypatch):
    """A second process must not delete/recreate while the first holds the lock."""
    import subprocess
    import sys
    import threading

    store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())
    script = """
import sys
from pathlib import Path
from server import reconstruction_review_store as store
store._path = lambda: Path(sys.argv[1])
with store._store_guard():
    print("LOCKED", flush=True)
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(local_store)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "LOCKED"
        finished = threading.Event()
        errors = []

        def delete():
            try:
                store.clear_saved_reconstruction_review()
            except Exception as exc:
                errors.append(exc)
            finally:
                finished.set()

        worker = threading.Thread(target=delete, daemon=True)
        worker.start()
        assert not finished.wait(0.2), "deletion bypassed cross-process lock"
        child.stdin.write("\n")
        child.stdin.flush()
        assert finished.wait(5), "deletion remained blocked after lock release"
        worker.join(timeout=5)
        assert not errors
        assert not local_store.exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_cross_process_lock_path_symlink_is_rejected(local_store, tmp_path):
    """Do not follow a substituted lock path, including on Windows."""
    lock_path = local_store.with_name(store._FILENAME + ".lock")
    unrelated = tmp_path / "unrelated-lock-target"
    unrelated.write_text("leave intact")
    try:
        lock_path.symlink_to(unrelated)
    except (OSError, NotImplementedError):
        pytest.skip("creating symlinks is unavailable on this runner")
    with pytest.raises(OSError):
        store.save_reconstruction_review({"mode": "all_saved"}, 1, _envelope())
    assert unrelated.read_text() == "leave intact"
    assert not local_store.exists()


def test_cross_process_revocation_before_locked_save_fails_closed(local_store, tmp_path):
    """A separate process must recheck revoked retention before persisting labels.

    The child passes its initial consent check, then waits while the parent
    revokes the shared policy. This is a real subprocess boundary rather than
    a monkeypatched thread-local permission change.
    """
    import subprocess
    import sys

    consent = tmp_path / "retention-granted"
    consent.write_text("granted")
    script = """
import sys
from pathlib import Path
from server import reconstruction_review_store as store
store._path = lambda: Path(sys.argv[1])
consent = Path(sys.argv[2])
checks = [0]
def retention_allowed():
    checks[0] += 1
    if checks[0] == 1:
        permitted = consent.exists()
        print("INITIAL_CHECK", flush=True)
        sys.stdin.readline()
        return permitted
    return consent.exists()
store._retention_allowed = retention_allowed
try:
    store.save_reconstruction_review({"mode": "all_saved"}, 1, {
        "evidence_sha256": "a" * 64,
        "annotations": [{"event_id": "event-1", "true_resource_pairs": []}],
    })
except PermissionError:
    print("REVOKED", flush=True)
else:
    print("UNSAFE_SAVE", flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(local_store), str(consent)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "INITIAL_CHECK"
        consent.unlink()
        child.stdin.write("\n")
        child.stdin.flush()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "REVOKED", stdout
        assert not local_store.exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_cross_process_revocation_during_atomic_replace_removes_snapshot(local_store, tmp_path):
    """Revocation in another process after replace must prevent save acknowledgement.

    The child writes the snapshot and pauses inside os.replace, while the
    parent withdraws retention. This exercises the post-replace policy check
    across a process boundary, not just an in-process monkeypatch.
    """
    import subprocess
    import sys

    consent = tmp_path / "retention-granted"
    consent.write_text("granted")
    script = """
import os
import sys
from pathlib import Path
from server import reconstruction_review_store as store
store._path = lambda: Path(sys.argv[1])
consent = Path(sys.argv[2])
store._retention_allowed = lambda: consent.exists()
original_replace = os.replace
def paused_replace(source, destination):
    original_replace(source, destination)
    print("REPLACED", flush=True)
    sys.stdin.readline()
os.replace = paused_replace
try:
    store.save_reconstruction_review({"mode": "all_saved"}, 1, {
        "evidence_sha256": "a" * 64,
        "annotations": [{"event_id": "event-1", "true_resource_pairs": []}],
    })
except PermissionError:
    print("REVOKED", flush=True)
else:
    print("UNSAFE_SAVE", flush=True)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, str(local_store), str(consent)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "REPLACED"
        assert local_store.exists(), "child did not reach the atomic replace"
        consent.unlink()
        child.stdin.write("\n")
        child.stdin.flush()
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        assert stdout.strip() == "REVOKED", stdout
        assert not local_store.exists(), "revoked labels survived the post-write check"
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


def test_cross_process_delete_after_atomic_replace_wins(local_store):
    """A separate deletion must wait for the writer's lock and remove its snapshot."""
    import subprocess
    import sys
    import time

    writer_script = """
import os
import sys
from pathlib import Path
from server import reconstruction_review_store as store
store._path = lambda: Path(sys.argv[1])
store._retention_allowed = lambda: True
original_replace = os.replace
def paused_replace(source, destination):
    original_replace(source, destination)
    print("REPLACED", flush=True)
    sys.stdin.readline()
os.replace = paused_replace
store.save_reconstruction_review({"mode": "all_saved"}, 1, {
    "evidence_sha256": "a" * 64,
    "annotations": [{"event_id": "event-1", "true_resource_pairs": []}],
})
print("SAVED", flush=True)
"""
    delete_script = """
import sys
from pathlib import Path
from server import reconstruction_review_store as store
store._path = lambda: Path(sys.argv[1])
print("DELETING", flush=True)
store.clear_saved_reconstruction_review()
print("DELETED", flush=True)
"""
    writer = subprocess.Popen(
        [sys.executable, "-c", writer_script, str(local_store)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    deleter = None
    try:
        assert writer.stdout.readline().strip() == "REPLACED"
        assert local_store.exists()
        deleter = subprocess.Popen(
            [sys.executable, "-c", delete_script, str(local_store)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        assert deleter.stdout.readline().strip() == "DELETING"
        time.sleep(0.2)
        assert deleter.poll() is None, "delete bypassed the writer lock"
        writer.stdin.write(chr(10))
        writer.stdin.flush()
        writer_out, writer_err = writer.communicate(timeout=10)
        assert writer.returncode == 0, writer_err
        assert writer_out.strip() == "SAVED"
        delete_out, delete_err = deleter.communicate(timeout=10)
        assert deleter.returncode == 0, delete_err
        assert delete_out.strip() == "DELETED"
        assert not local_store.exists(), "deletion must win after writer unlock"
    finally:
        for child in (writer, deleter):
            if child is not None and child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_grant_revoked_during_atomic_replace_removes_saved_labels(local_store, monkeypatch):
    """Changing history access during persistence must not leave review labels."""
    grant = {"mode": "all_saved"}
    original_replace = store.os.replace

    def revoke_during_replace(src, dst):
        original_replace(src, dst)
        grant["mode"] = "none"

    monkeypatch.setattr(store.os, "replace", revoke_during_replace)
    with pytest.raises(PermissionError, match="consent or retention was revoked"):
        store.save_reconstruction_review(grant, 1, _envelope())
    assert not local_store.exists()
