from datetime import datetime, timedelta, timezone

import pytest

from server import open_work_run_case_binding as binding
from server.open_work_case_declarations import CaseDeclarationError

SCOPE = "owg:scope:" + "a" * 32
CASE = "owg:w:" + "b" * 32
RUN = "owg:run:" + "c" * 32
FINGERPRINT = "d" * 64
SECRET = "local-test-secret-at-least-16"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(binding, "auth_dir", lambda: tmp_path)
    monkeypatch.setattr(binding, "read_policy", lambda: {"human_retention": {"mode": "days", "days": 3}})
    return tmp_path


def arguments(actor="operator-a", fingerprint=FINGERPRINT, revision=1):
    return dict(scope=SCOPE, actor=actor, secret=SECRET, case_ref=CASE,
                run_ref=RUN, fingerprint=fingerprint, revision=revision)


def test_durable_binding_is_actor_scoped_and_revocable(ledger):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    assert binding.get_binding(**arguments(), now=now) is None
    assert not list(ledger.iterdir())
    created = binding.bind_run(**arguments(), now=now)
    assert created["association"] == "operator_confirmed"
    assert created["run_case_identity_independently_verified"] is False
    assert created["customer_outcome_verified"] is False
    assert binding.get_binding(**arguments(), now=now) == created
    assert binding.get_binding(**arguments(actor="operator-b"), now=now) is None
    assert binding.get_binding(**arguments(fingerprint="e" * 64), now=now) is None
    assert binding.get_binding(**arguments(revision=2), now=now) is None
    assert binding.revoke_binding(scope=SCOPE, actor="operator-b", secret=SECRET,
                                  case_ref=CASE, run_ref=RUN) is False
    assert binding.revoke_binding(scope=SCOPE, actor="operator-a", secret=SECRET,
                                  case_ref=CASE, run_ref=RUN) is True
    assert binding.get_binding(**arguments(), now=now) is None


def test_expiry_and_retention_policy_fail_closed(ledger, monkeypatch):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    binding.bind_run(**arguments(), now=now)
    assert binding.get_binding(**arguments(), now=now + timedelta(days=4)) is None
    monkeypatch.setattr(binding, "read_policy", lambda: {"human_retention": {"mode": "disabled"}})
    with pytest.raises(CaseDeclarationError):
        binding.get_binding(**arguments(), now=now)


def test_conflicting_rebinding_requires_revocation(ledger):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    binding.bind_run(**arguments(), now=now)
    with pytest.raises(CaseDeclarationError):
        binding.bind_run(**arguments(fingerprint="e" * 64), now=now)
    assert binding.get_binding(**arguments(), now=now) is not None


def test_rejects_invalid_scopes_and_symlink(ledger):
    with pytest.raises(CaseDeclarationError):
        binding.bind_run(**arguments(actor=""), now=datetime.now(timezone.utc))
    outside = ledger.parent / (ledger.name + "-outside")
    outside.write_text("not a database")
    (ledger / binding._DB).symlink_to(outside)
    with pytest.raises(CaseDeclarationError):
        binding.bind_run(**arguments(), now=datetime.now(timezone.utc))
