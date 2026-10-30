"""Local owner-declared case facts: exact versions, provenance and lifecycle."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os

import pytest

from server import open_work_case_declarations as facts
from server import open_work_reviews as reviews

A = "owg:w:" + "a" * 32
B = "owg:w:" + "b" * 32
C = "owg:w:" + "c" * 32
SCOPE = "owg:scope:" + "1" * 32
OTHER_SCOPE = "owg:scope:" + "2" * 32
NOW = datetime(2026, 10, 30, 9, tzinfo=timezone.utc)


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_OBSERVER_AUTH_DIR", str(tmp_path))
    policy = lambda: {"human_retention": {"mode": "forever"}}
    monkeypatch.setattr(facts, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    return tmp_path


def reviewed(ref, at=NOW):
    reviews.save_review_intention(ref, "assess_follow_up",
        provider_candidate_currently_open=True, now=at)


def save(ref=A, scope=SCOPE, **kwargs):
    return facts.save_case_declaration(
        ref, scope, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=NOW, **kwargs)


def test_default_off_requires_prior_explicit_review_and_never_reads_source(local):
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []
    assert not facts._path().exists()
    with pytest.raises(facts.CaseDeclarationError, match="local review"):
        save()
    assert not facts._path().exists()
    reviewed(A)
    item = save()
    assert item["revision"] == 1
    assert item["provenance"] == "explicit_owner_declaration_not_source_verified"
    assert item["source_state"] == "unknown_until_explicit_recheck"
    for key in ("expected_outcome_verified", "business_obligation_verified",
                "customer_outcome_verified", "agent_execution_authorized"):
        assert item[key] is False
    listing = facts.list_case_declarations(SCOPE, now=NOW)
    assert listing["source_read_performed"] is False
    assert listing["not_synced"] is True
    assert listing["items"] == [item]
    assert facts.list_case_declarations(OTHER_SCOPE, now=NOW)["items"] == []
    if os.name == "posix":
        assert facts._path().stat().st_mode & 0o777 == 0o600
    payload = facts._path().read_bytes()
    for secret in (b"in_1234567890ABCDEF", b"customer@example.com", b"api_key", b"acct_"):
        assert secret not in payload


def test_exact_revision_no_implicit_renewal_and_no_partial_stale_write(local):
    reviewed(A)
    first = save()
    later = NOW + timedelta(hours=1)
    updated = facts.save_case_declaration(A, SCOPE,
        goal_code="verify_operation", expected_outcome_code="operation_receipt_reviewed",
        expected_revision=1, now=later)
    assert updated["revision"] == 2
    assert updated["created_at"] == first["created_at"]
    assert updated["expires_at"] == first["expires_at"]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(expected_revision=1)
    assert facts.list_case_declarations(SCOPE, now=later)["items"][0] == updated


def test_dependency_scope_review_and_cycle_guard(local):
    for ref in (A, B, C):
        reviewed(ref)
    a = save()
    b = save(B, depends_on_work_item_refs=[A])
    c = save(C, depends_on_work_item_refs=[B])
    assert b["depends_on_work_item_refs"] == [A]
    assert c["depends_on_work_item_refs"] == [B]
    with pytest.raises(facts.CaseDeclarationError, match="cyclic"):
        save(expected_revision=a["revision"], depends_on_work_item_refs=[C])
    with pytest.raises(facts.CaseDeclarationError, match="source scope"):
        save(expected_revision=a["revision"], scope=OTHER_SCOPE)
    with pytest.raises(facts.CaseDeclarationError, match="duplicate"):
        save(expected_revision=a["revision"], depends_on_work_item_refs=[B, B])
    with pytest.raises(facts.CaseDeclarationError, match="self-referential"):
        save(expected_revision=a["revision"], depends_on_work_item_refs=[A])
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"]


def test_dependency_requires_same_scope_and_review(local):
    reviewed(A)
    reviewed(B)
    save()
    with pytest.raises(facts.CaseDeclarationError, match="dependency missing"):
        save(B, depends_on_work_item_refs=[A], scope=OTHER_SCOPE)
    reviewed(C)
    with pytest.raises(facts.CaseDeclarationError, match="dependency missing"):
        save(C, depends_on_work_item_refs=[B])
    assert len(facts.list_case_declarations(SCOPE, now=NOW)["items"]) == 1


def test_review_removal_erases_transitive_dependent_declarations(local):
    # B -> A and C -> B were explicitly owner-declared. Removing A's
    # review must not silently turn B (and therefore C) into unblocked work.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    save()
    b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    assert {i["work_item_ref"] for i in facts.list_case_declarations(SCOPE, now=NOW)["items"]} == {A, B, C, d}

    assert reviews.remove_review_intention(A, now=NOW) is True
    remaining = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert remaining == [independent]
    # B and C retain their human review requests, but not the now-invalid
    # owner declarations or their previously approved dependency graph.
    reviewed_refs = {i["work_item_ref"] for i in reviews.list_review_intentions(now=NOW)["items"]}
    assert {B, C, d}.issubset(reviewed_refs)
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(B, expected_revision=b["revision"])
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]

    reviews.clear_review_intentions()
    assert not facts._path().exists()
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []



def test_review_disconnected_between_admission_snapshot_and_write(local, monkeypatch):
    reviewed(A)
    original = facts._reviewed
    calls = 0

    def disconnect_after_first_snapshot(at):
        nonlocal calls
        snapshot = original(at)
        calls += 1
        if calls == 1:
            reviews.clear_review_intentions()
        return snapshot

    monkeypatch.setattr(facts, "_reviewed", disconnect_after_first_snapshot)
    with pytest.raises(facts.CaseDeclarationError, match="review revoked"):
        save()
    assert calls >= 2
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []


def test_dependency_review_removed_between_snapshot_and_write(local, monkeypatch):
    reviewed(A)
    reviewed(B)
    save()
    original = facts._reviewed
    calls = 0

    def remove_dependency_after_first_snapshot(at):
        nonlocal calls
        snapshot = original(at)
        calls += 1
        if calls == 1:
            assert reviews.remove_review_intention(A, now=at)
        return snapshot

    monkeypatch.setattr(facts, "_reviewed", remove_dependency_after_first_snapshot)
    with pytest.raises(facts.CaseDeclarationError, match="review revoked"):
        save(B, depends_on_work_item_refs=[A])
    assert {i["work_item_ref"] for i in facts.list_case_declarations(SCOPE, now=NOW)["items"]} == set()


def test_retention_revoked_between_snapshot_and_write(local, monkeypatch):
    reviewed(A)
    original = facts._reviewed
    calls = 0

    def revoke_retention_after_first_snapshot(at):
        nonlocal calls
        snapshot = original(at)
        calls += 1
        if calls == 1:
            monkeypatch.setattr(facts, "read_policy",
                lambda: {"human_retention": {"mode": "ephemeral"}})
        return snapshot

    monkeypatch.setattr(facts, "_reviewed", revoke_retention_after_first_snapshot)
    with pytest.raises(facts.CaseDeclarationError, match="retention revoked"):
        save()
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []

def test_retention_shrink_and_session_only_scrub(local, monkeypatch):
    reviewed(A)
    save()
    policy = lambda: {"human_retention": {"mode": "days", "days": 1}}
    monkeypatch.setattr(facts, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    assert facts.list_case_declarations(SCOPE, now=NOW + timedelta(days=2))["items"] == []
    policy = lambda: {"human_retention": {"mode": "ephemeral"}}
    monkeypatch.setattr(facts, "read_policy", policy)
    monkeypatch.setattr(reviews, "read_policy", policy)
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []
    assert not facts._path().exists()
    with pytest.raises(facts.CaseDeclarationError, match="retained human history"):
        save()


def test_storage_symlink_and_permissions_fail_closed(local):
    reviewed(A)
    target = local / "other"
    target.write_text("untouched")
    try:
        facts._path().symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    with pytest.raises(facts.CaseDeclarationError, match="unsafe"):
        save()
    assert target.read_text() == "untouched"
    facts._path().unlink()
    save()
    if os.name == "posix":
        facts._path().chmod(0o644)
        with pytest.raises(facts.CaseDeclarationError, match="permissions"):
            facts.list_case_declarations(SCOPE, now=NOW)


def test_two_concurrent_edits_with_same_revision_cannot_both_commit(local):
    reviewed(A)
    save()
    def edit(goal):
        try:
            return facts.save_case_declaration(A, SCOPE, goal_code=goal,
                expected_outcome_code="source_state_rechecked",
                expected_revision=1, now=NOW + timedelta(minutes=1))
        except facts.CaseDeclarationError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ("verify_operation", "assess_declared_policy")))
    assert sum(isinstance(v, dict) for v in results) == 1
    assert sum(isinstance(v, str) and "stale" in v for v in results) == 1
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"][0]["revision"] == 2


@pytest.mark.parametrize("bad", ["owg:w:" + "A" * 32, "in_1234567890ABCDEF", "owg:w:" + "a" * 31])
def test_ref_validation_precedes_storage(local, bad):
    with pytest.raises(facts.CaseDeclarationError):
        save(ref=bad)
    assert not facts._path().exists()


def test_sqlite_rollback_preserves_existing_fact(local):
    reviewed(A)
    reviewed(B)
    first = save()
    with pytest.raises(facts.CaseDeclarationError):
        save(B, depends_on_work_item_refs=[A], expected_revision=1)
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [first]


def test_corrupt_declaration_db_cannot_block_explicit_review_deletion(local):
    reviewed(A)
    facts._path().write_bytes(b"not a SQLite database")
    if os.name == "posix":
        facts._path().chmod(0o600)
    assert reviews.remove_review_intention(A, now=NOW) is True
    assert not facts._path().exists()
    assert A not in {i["work_item_ref"] for i in reviews.list_review_intentions(now=NOW)["items"]}



def test_expired_dependency_suppresses_transitive_dependents_but_not_independent_cases(local):
    # A's review is renewed, but its declaration is not. B and C remain
    # individually unexpired, yet neither may reference a hidden ancestor.
    d = "owg:w:" + "d" * 32
    reviewed(A)
    save()
    at_b = NOW + timedelta(days=1)
    reviewed(A, at=at_b)
    reviewed(B, at=at_b)
    facts.save_case_declaration(B, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], now=at_b)
    at_c = NOW + timedelta(days=2)
    reviewed(C, at=at_c)
    reviewed(d, at=at_c)
    facts.save_case_declaration(C, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[B], now=at_c)
    facts.save_case_declaration(d, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=at_c)

    before = facts.list_case_declarations(SCOPE, now=NOW + timedelta(days=6))
    assert {i["work_item_ref"] for i in before["items"]} == {A, B, C, d}
    after = facts.list_case_declarations(SCOPE, now=NOW + timedelta(days=7, hours=1))
    assert {i["work_item_ref"] for i in after["items"]} == {d}
    assert after["items"][0]["depends_on_work_item_refs"] == []


def test_expired_dependency_cannot_be_presented_as_live_declared_edge(local):
    reviewed(A)
    save()
    reviewed(A, at=NOW + timedelta(days=1))  # Review renewed, declaration not.
    reviewed(B, at=NOW + timedelta(days=1))
    facts.save_case_declaration(B, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], now=NOW + timedelta(days=1))
    at = NOW + timedelta(days=7, hours=1)
    assert A in {i["work_item_ref"] for i in reviews.list_review_intentions(now=at)["items"]}
    assert facts.list_case_declarations(SCOPE, now=at)["items"] == []


def test_lapsed_review_cannot_resurrect_a_later_saved_declaration(local):
    # A review expires seven days after its own creation. A declaration
    # saved later must not gain an extra seven days of authority.
    reviewed(A)
    saved_at = NOW + timedelta(days=1)
    item = facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=saved_at)
    assert item["expires_at"] == (NOW + timedelta(days=7)).isoformat()
    assert facts.list_case_declarations(SCOPE, now=NOW + timedelta(days=6))["items"] == [item]

    after_lapse = NOW + timedelta(days=7, hours=1)
    assert facts.list_case_declarations(SCOPE, now=after_lapse)["items"] == []
    reviewed(A, at=after_lapse)
    # A newly saved review is not a reapproval of the old goal/outcome.
    assert facts.list_case_declarations(SCOPE, now=after_lapse)["items"] == []
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(A, SCOPE,
            goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            expected_revision=item["revision"], now=after_lapse)
    fresh = facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=after_lapse)
    assert fresh["revision"] == 1
    assert fresh["created_at"] == after_lapse.isoformat()


def test_dependency_expiry_cascades_and_cas_update_never_renews(local):
    # B is reviewed later than A; B cannot outlive A's declared prerequisite
    # and reappear merely because A is re-reviewed after expiry.
    reviewed(A)
    later = NOW + timedelta(days=1)
    reviewed(B, at=later)
    a = facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=later)
    b = facts.save_case_declaration(B, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=later)
    assert b["expires_at"] == (later + timedelta(days=7)).isoformat()

    linked = facts.save_case_declaration(B, SCOPE,
        goal_code="assess_declared_policy",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], expected_revision=1,
        now=later + timedelta(hours=1))
    assert linked["expires_at"] == a["expires_at"]
    # Removing the dependency by explicit CAS must not silently renew B.
    unlinked = facts.save_case_declaration(B, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        expected_revision=2, now=later + timedelta(hours=2))
    assert unlinked["expires_at"] == a["expires_at"]

    # Reapprove B -> A before expiry; even this cannot extend the deadline.
    linked_again = facts.save_case_declaration(B, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], expected_revision=3,
        now=later + timedelta(hours=3))
    assert linked_again["expires_at"] == a["expires_at"]

    after_lapse = NOW + timedelta(days=7, hours=1)
    reviewed(A, at=after_lapse)
    facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=after_lapse)
    items = facts.list_case_declarations(SCOPE, now=after_lapse)["items"]
    assert {item["work_item_ref"] for item in items} == {A}
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(B, SCOPE,
            goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            expected_revision=linked_again["revision"], now=after_lapse)


def test_shortened_prerequisite_expiry_cannot_resurrect_old_dependent(local):
    # D is older than A. Adding A -> D shortens A's expiry after B -> A
    # was approved. When A expires, a fresh A must not revive stale B -> A.
    d = "owg:w:" + "d" * 32
    early = NOW - timedelta(days=2)
    reviewed(d, at=early)
    dependency = facts.save_case_declaration(d, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=early)
    reviewed(A)
    reviewed(B)
    first_a = save()
    old_b = save(B, depends_on_work_item_refs=[A])
    assert old_b["expires_at"] == first_a["expires_at"]

    shortened_a = facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[d], expected_revision=1,
        now=NOW + timedelta(days=1))
    assert shortened_a["expires_at"] == dependency["expires_at"]
    at = NOW + timedelta(days=5, hours=1)
    assert facts.list_case_declarations(SCOPE, now=at)["items"] == []

    fresh_a = facts.save_case_declaration(A, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=at)
    assert fresh_a["revision"] == 1
    assert {i["work_item_ref"] for i in facts.list_case_declarations(SCOPE, now=at)["items"]} == {A}
    # The old B revision was transitively erased by the write-time prune.
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(B, SCOPE,
            goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            depends_on_work_item_refs=[A], expected_revision=old_b["revision"],
            now=at)


def test_missing_prerequisite_cannot_resurrect_dependent_on_redeclaration(local):
    # Simulate an interrupted/partial auxiliary-ledger cleanup: A is gone
    # while B -> A and C -> B remain. Read-time hiding alone is insufficient
    # because recreating A could otherwise revive those old approvals.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    save()
    old_b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    with facts._database(create=False) as db:
        assert db is not None
        db.execute("DELETE FROM declarations WHERE work_item_ref=?", (A,))

    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]
    recreated = save()  # Prunes the orphan closure before inserting A.
    assert recreated["revision"] == 1
    remaining = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert {item["work_item_ref"] for item in remaining} == {A, d}
    assert independent in remaining
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(B, expected_revision=old_b["revision"], depends_on_work_item_refs=[A])


def test_wrong_scope_prerequisite_prunes_dependents_not_unrelated_cases(local):
    # A declaration row changed to a different scope cannot lend authority
    # to B -> A, nor to the transitive C -> B edge.
    d = "owg:w:" + "d" * 32
    e = "owg:w:" + "e" * 32
    for ref in (A, B, C, d, e):
        reviewed(ref)
    save()
    old_b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    with facts._database(create=False) as db:
        assert db is not None
        db.execute("UPDATE declarations SET scope_ref=? WHERE work_item_ref=?",
                   (OTHER_SCOPE, A))

    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]
    new_case = save(e)  # Triggers fail-closed scope-aware pruning.
    remaining = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert {item["work_item_ref"] for item in remaining} == {d, e}
    assert independent in remaining and new_case in remaining
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(B, expected_revision=old_b["revision"])
