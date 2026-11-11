from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from server import open_work_run_receipts as receipts
from server import open_work_run_case_binding as binding
from server.open_work_case_declarations import CaseDeclarationError

SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
SECRET = "test-secret-long-enough"
FINGERPRINT = "d" * 64
DIGEST = "e" * 64


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(receipts, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(binding, "read_policy", lambda: {
        "human_retention": {"mode": "days", "days": 3}})
    return tmp_path


def args(actor="operator", revision=1, fingerprint=FINGERPRINT):
    return dict(scope=SCOPE, actor=actor, secret=SECRET, case_ref=CASE,
                run_ref=RUN, fingerprint=fingerprint, revision=revision)


def test_review_correction_retraction_and_actor_isolation(store):
    now = datetime.now(timezone.utc)
    assert receipts.review(**args(), now=now)["entries"] == []
    assert list(store.iterdir()) == []
    first = receipts.append(**args(), action="reviewed", evidence_digest=DIGEST, now=now)
    assert first["active_review_count"] == 1
    assert first["customer_outcome_verified"] is False
    assert receipts.review(**args(actor="other"), now=now)["entries"] == []
    entry = first["entries"][0]["id"]
    correction = receipts.append(**args(), action="disputed", evidence_digest=DIGEST,
                                 supersedes=entry, now=now + timedelta(seconds=1))
    assert correction["active_review_count"] == 1
    assert len(correction["entries"]) == 2
    with pytest.raises(CaseDeclarationError):
        receipts.append(**args(), action="reviewed", evidence_digest=DIGEST,
                        supersedes=entry, now=now + timedelta(seconds=2))
    last = correction["entries"][-1]["id"]
    revoked = receipts.append(**args(), action="retracted", evidence_digest=DIGEST,
                              supersedes=last, now=now + timedelta(seconds=2))
    assert revoked["active_review_count"] == 0
    assert len(revoked["entries"]) == 3
    assert receipts.review(**args(revision=2), now=now)["entries"] == []
    assert receipts.review(**args(fingerprint="f" * 64), now=now)["entries"] == []


def test_expiry_retention_and_scope_revocation(store, monkeypatch):
    now = datetime.now(timezone.utc)
    receipts.append(**args(), action="reviewed", evidence_digest=DIGEST, now=now)
    assert receipts.review(**args(), now=now + timedelta(days=4))["entries"] == []
    assert receipts.clear_scope(SCOPE) == 1
    assert receipts.review(**args(), now=now)["entries"] == []
    monkeypatch.setattr(binding, "read_policy", lambda: {
        "human_retention": {"mode": "disabled"}})
    with pytest.raises(CaseDeclarationError):
        receipts.review(**args(), now=now)


def test_hmac_tamper_fails_closed_and_no_write(store):
    now = datetime.now(timezone.utc)
    receipts.append(**args(), action="reviewed", evidence_digest=DIGEST, now=now)
    db = sqlite3.connect(str(store / receipts._NAME))
    db.execute("UPDATE receipts SET action='disputed'")
    db.commit()
    db.close()
    with pytest.raises(CaseDeclarationError, match="integrity"):
        receipts.review(**args(), now=now)
    with pytest.raises(CaseDeclarationError, match="integrity"):
        receipts.append(**args(), action="reviewed", evidence_digest=DIGEST, now=now)


def test_invalid_inputs_and_guard_denial(store):
    now = datetime.now(timezone.utc)
    with pytest.raises(CaseDeclarationError):
        receipts.append(**args(), action="retracted", evidence_digest=DIGEST, now=now)
    with pytest.raises(CaseDeclarationError):
        receipts.append(**args(), action="reviewed", evidence_digest="not-a-hash", now=now)
    with pytest.raises(CaseDeclarationError):
        receipts.append(**args(), action="reviewed", evidence_digest=DIGEST,
                        guard=lambda: (_ for _ in ()).throw(CaseDeclarationError("revoked")),
                        now=now)
    assert not list(store.iterdir())


def test_symlink_denied(store):
    outside = store.parent / (store.name + "-other")
    outside.write_text("not a database")
    (store / receipts._NAME).symlink_to(outside)
    with pytest.raises(CaseDeclarationError, match="unsafe"):
        receipts.review(**args())


def test_existing_permissive_receipt_ledger_rejected_without_chmod(store):
    import os
    if os.name != "posix":
        pytest.skip("POSIX permissions only")
    target = store / receipts._NAME
    target.write_bytes(b"sentinel")
    target.chmod(0o644)
    with pytest.raises(CaseDeclarationError, match="unsafe"):
        receipts.review(**args())
    assert target.stat().st_mode & 0o777 == 0o644
    assert target.read_bytes() == b"sentinel"


def test_hardlinked_receipt_ledger_rejected_without_touching_target(store):
    outside = store.parent / (store.name + "-hardlink")
    outside.write_bytes(b"sentinel")
    (store / receipts._NAME).hardlink_to(outside)
    with pytest.raises(CaseDeclarationError, match="unsafe"):
        receipts.review(**args())
    assert outside.read_bytes() == b"sentinel"


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
@pytest.mark.parametrize("sidecar_type", ["regular", "dangling_symlink"])
def test_run_receipt_sidecar_rejected_without_mutation(store, suffix, sidecar_type):
    target = store / receipts._NAME
    target.touch(mode=0o600)
    sidecar = store / (receipts._NAME + suffix)
    if sidecar_type == "regular":
        sidecar.write_bytes(b"retained-evidence")
    else:
        sidecar.symlink_to(store / "missing-sidecar-target")
    with pytest.raises(CaseDeclarationError, match="sidecar"):
        receipts.review(**args())
    if sidecar_type == "regular":
        assert sidecar.read_bytes() == b"retained-evidence"
    else:
        assert sidecar.is_symlink()


def test_run_receipt_wal_mode_refused(store):
    target = store / receipts._NAME
    with sqlite3.connect(str(target)) as db:
        assert db.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with pytest.raises(CaseDeclarationError, match="mode"):
        receipts.review(**args())
