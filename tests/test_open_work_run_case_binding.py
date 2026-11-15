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



def test_bulk_case_revoke_is_actor_and_case_scoped_across_connections(ledger):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    other_case = "owg:w:" + "e" * 32
    other_scope = "owg:scope:" + "f" * 32
    other_run = "owg:run:" + "f" * 32
    binding.bind_run(**arguments(), now=now)
    binding.bind_run(**{**arguments(), "run_ref": other_run}, now=now)
    binding.bind_run(**arguments(actor="operator-b"), now=now)
    binding.bind_run(**{**arguments(), "case_ref": other_case}, now=now)
    binding.bind_run(**{**arguments(), "scope": other_scope}, now=now)
    assert binding.revoke_case_bindings(
        scope=SCOPE, actor="operator-a", secret=SECRET, case_ref=CASE,
    ) == 2
    assert binding.get_binding(**arguments(), now=now) is None
    assert binding.get_binding(**{**arguments(), "run_ref": other_run}, now=now) is None
    assert binding.get_binding(**arguments(actor="operator-b"), now=now) is not None
    assert binding.get_binding(**{**arguments(), "case_ref": other_case}, now=now) is not None
    assert binding.get_binding(**{**arguments(), "scope": other_scope}, now=now) is not None
    assert binding.revoke_case_bindings(
        scope=SCOPE, actor="operator-a", secret=SECRET, case_ref=CASE,
    ) == 0


def test_bulk_case_revoke_absent_store_and_invalid_scope(ledger):
    assert binding.revoke_case_bindings(
        scope=SCOPE, actor="operator-a", secret=SECRET, case_ref=CASE,
    ) == 0
    assert not list(ledger.iterdir())
    with pytest.raises(CaseDeclarationError):
        binding.revoke_case_bindings(
            scope="owg:scope:../other", actor="operator-a",
            secret=SECRET, case_ref=CASE,
        )
    assert not list(ledger.iterdir())


def test_case_inventory_is_bounded_actor_scoped_and_revision_sensitive(ledger):
    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    other_case = "owg:w:" + "e" * 32
    other_scope = "owg:scope:" + "f" * 32
    other_run = "owg:run:" + "f" * 32
    empty = binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                       case_ref=CASE, revision=1, now=now)
    assert empty["items"] == []
    assert not list(ledger.iterdir())
    binding.bind_run(**arguments(), now=now)
    binding.bind_run(**{**arguments(), "run_ref": other_run}, now=now)
    binding.bind_run(**arguments(actor="operator-b"), now=now)
    binding.bind_run(**{**arguments(), "case_ref": other_case}, now=now)
    binding.bind_run(**{**arguments(), "scope": other_scope}, now=now)
    result = binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                        case_ref=CASE, revision=1, now=now)
    assert [x["run_ref"] for x in result["items"]] == sorted([RUN, other_run])
    assert all(x["declaration_state"] == "current_revision" for x in result["items"])
    assert result["source_read_performed"] is False
    assert result["run_snapshots_rechecked"] is False
    assert result["agent_execution_authorized"] is False
    assert binding.list_case_bindings(scope=SCOPE, actor="operator-b", secret=SECRET,
                                      case_ref=CASE, revision=1, now=now)["items"] == [result["items"][0]]
    stale = binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                       case_ref=CASE, revision=2, now=now)
    assert all(x["declaration_state"] == "stale_revision" for x in stale["items"])
    assert binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                      case_ref=CASE, revision=1,
                                      now=now + timedelta(days=4))["items"] == []
    assert binding.revoke_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                        case_ref=CASE) == 2
    assert binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                      case_ref=CASE, revision=1, now=now)["items"] == []


def test_inventory_retention_and_invalid_scope_fail_closed(ledger, monkeypatch):
    with pytest.raises(CaseDeclarationError):
        binding.list_case_bindings(scope="../other", actor="operator-a", secret=SECRET,
                                   case_ref=CASE, revision=1)
    assert not list(ledger.iterdir())
    monkeypatch.setattr(binding, "read_policy", lambda: {"human_retention": {"mode": "disabled"}})
    with pytest.raises(CaseDeclarationError):
        binding.list_case_bindings(scope=SCOPE, actor="operator-a", secret=SECRET,
                                   case_ref=CASE, revision=1)
    assert not list(ledger.iterdir())


def test_shrinking_retention_revokes_existing_binding_and_inventory(ledger, monkeypatch):
    """A shorter policy takes effect immediately, not at the original expiry."""
    start = datetime(2026, 11, 3, tzinfo=timezone.utc)
    binding.bind_run(**arguments(), now=start)
    later = start + timedelta(days=2)
    assert binding.get_binding(**arguments(), now=later) is not None
    assert binding.list_case_bindings(
        scope=SCOPE, actor="operator-a", secret=SECRET,
        case_ref=CASE, revision=1, now=later)["items"]

    monkeypatch.setattr(binding, "read_policy",
                        lambda: {"human_retention": {"mode": "days", "days": 1}})
    assert binding.get_binding(**arguments(), now=later) is None
    assert binding.list_case_bindings(
        scope=SCOPE, actor="operator-a", secret=SECRET,
        case_ref=CASE, revision=1, now=later)["items"] == []
    monkeypatch.setattr(binding, "_now", lambda ignored: later)
    with pytest.raises(CaseDeclarationError):
        binding.require_exclusive_run_case_binding(**arguments())
    # An expired binding must not block an explicit fresh operator review.
    binding.bind_run(**arguments(), now=later)
    assert binding.get_binding(**arguments(), now=later) is not None
    binding.require_exclusive_run_case_binding(**arguments())


def test_tightened_retention_preserves_other_actor_isolation(ledger, monkeypatch):
    start = datetime(2026, 11, 3, tzinfo=timezone.utc)
    later = start + timedelta(days=2)
    binding.bind_run(**arguments(actor="old"), now=start)
    binding.bind_run(**arguments(actor="fresh"), now=later)
    monkeypatch.setattr(binding, "read_policy",
                        lambda: {"human_retention": {"mode": "days", "days": 1}})
    assert binding.get_binding(**arguments(actor="old"), now=later) is None
    assert binding.get_binding(**arguments(actor="fresh"), now=later) is not None
