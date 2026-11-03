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


def test_disconnect_purges_all_actors_only_in_old_scope(ledger, monkeypatch):
    import asyncio
    from fastapi import HTTPException
    from server import open_work_declaration_routes as routes

    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    second = "owg:scope:" + "f" * 32
    binding.bind_run(**arguments(actor="operator-a"), now=now)
    binding.bind_run(**arguments(actor="operator-b"), now=now)
    binding.bind_run(**{**arguments(), "scope": second}, now=now)
    monkeypatch.setattr(routes, "_source_scope", lambda: second)
    monkeypatch.setattr(routes, "clear_operation_evidence", lambda: None)
    monkeypatch.setattr(routes, "clear_human_outcome_reviews", lambda: None)
    monkeypatch.setattr(routes, "clear_case_declarations", lambda: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(routes._recheck_source(SCOPE))
    assert error.value.status_code == 409
    assert binding.get_binding(**arguments(actor="operator-a"), now=now) is None
    assert binding.get_binding(**arguments(actor="operator-b"), now=now) is None
    assert binding.get_binding(**{**arguments(), "scope": second}, now=now) is not None
    assert binding.clear_binding_scope(SCOPE) == 0
    assert binding.clear_binding_scope(second) == 1
    assert binding.get_binding(**{**arguments(), "scope": second}, now=now) is None


def test_scope_purge_rejects_untrusted_scope_without_creating_store(ledger):
    with pytest.raises(CaseDeclarationError):
        binding.clear_binding_scope("owg:scope:../other")
    assert not list(ledger.iterdir())
    assert binding.clear_binding_scope(SCOPE) == 0
    assert not list(ledger.iterdir())
