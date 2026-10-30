"""Local owner-declared case facts: exact versions, provenance and lifecycle."""
from __future__ import annotations

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3

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
        expected_revision=1, expected_edit_token=first["edit_token"], now=later)
    assert updated["revision"] == 2
    assert updated["created_at"] == first["created_at"]
    assert updated["expires_at"] == first["expires_at"]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(expected_revision=1, expected_edit_token=first["edit_token"])
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
        save(expected_revision=a["revision"], expected_edit_token=a["edit_token"], depends_on_work_item_refs=[C])
    with pytest.raises(facts.CaseDeclarationError, match="source scope"):
        save(expected_revision=a["revision"], expected_edit_token=a["edit_token"], scope=OTHER_SCOPE)
    with pytest.raises(facts.CaseDeclarationError, match="duplicate"):
        save(expected_revision=a["revision"], expected_edit_token=a["edit_token"], depends_on_work_item_refs=[B, B])
    with pytest.raises(facts.CaseDeclarationError, match="self-referential"):
        save(expected_revision=a["revision"], expected_edit_token=a["edit_token"], depends_on_work_item_refs=[A])
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
        save(B, expected_revision=b["revision"], expected_edit_token=b["edit_token"])
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
    first = save()
    def edit(goal):
        try:
            return facts.save_case_declaration(A, SCOPE, goal_code=goal,
                expected_outcome_code="source_state_rechecked",
                expected_revision=1, expected_edit_token=first["edit_token"], now=NOW + timedelta(minutes=1))
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
        save(B, depends_on_work_item_refs=[A], expected_revision=1, expected_edit_token=first["edit_token"])
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
    # A expires before independently reviewed D. B and C must not
    # reference a missing prerequisite, even through multiple hops.
    d = "owg:w:" + "d" * 32
    reviewed(A)
    save()
    at_b = NOW + timedelta(days=1)
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


def test_early_re_review_invalidates_prior_owner_approvals_and_dependents(local):
    # A's human review is replaced before its TTL expires. That is not
    # renewed approval for A's old goal or B -> A / C -> B dependency facts.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    old_a = save()
    old_b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)

    at = NOW + timedelta(days=1)
    reviewed(A, at=at)
    assert facts.list_case_declarations(SCOPE, now=at)["items"] == [independent]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            expected_revision=old_a["revision"], expected_edit_token=old_a["edit_token"], now=at)

    # Pruning is durable: recreating A cannot resurrect the old dependent
    # approvals, even though B and C remain in the human review queue.
    fresh_a = facts.save_case_declaration(
        A, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=at)
    assert fresh_a["revision"] == 1
    assert {i["work_item_ref"] for i in
            facts.list_case_declarations(SCOPE, now=at)["items"]} == {A, d}
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            B, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            depends_on_work_item_refs=[A],
            expected_revision=old_b["revision"], expected_edit_token=old_b["edit_token"], now=at)
    fresh_b = facts.save_case_declaration(
        B, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], now=at)
    facts.save_case_declaration(
        C, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[B], now=at)
    assert fresh_b["depends_on_work_item_refs"] == [A]
    assert {i["work_item_ref"] for i in
            facts.list_case_declarations(SCOPE, now=at)["items"]} == {A, B, C, d}


def test_review_replaced_between_admission_snapshots_refuses_stale_cas(local, monkeypatch):
    reviewed(A)
    original = save()
    real_reviewed = facts._reviewed
    calls = 0
    at = NOW + timedelta(minutes=1)

    def replace_after_first_snapshot(timestamp):
        nonlocal calls
        snapshot = real_reviewed(timestamp)
        calls += 1
        if calls == 1:
            reviewed(A, at=at)
        return snapshot

    monkeypatch.setattr(facts, "_reviewed", replace_after_first_snapshot)
    with pytest.raises(facts.CaseDeclarationError, match="review changed"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="verify_operation",
            expected_outcome_code="operation_receipt_reviewed",
            expected_revision=original["revision"], expected_edit_token=original["edit_token"], now=at)
    assert calls >= 2
    # The failed transaction must not rewrite the old owner declaration;
    # its now-obsolete review generation also hides it on reads.
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
            expected_revision=item["revision"], expected_edit_token=item["edit_token"], now=after_lapse)
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
        depends_on_work_item_refs=[A], expected_revision=1, expected_edit_token=b["edit_token"],
        now=later + timedelta(hours=1))
    assert linked["expires_at"] == a["expires_at"]
    # Removing the dependency by explicit CAS must not silently renew B.
    unlinked = facts.save_case_declaration(B, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        expected_revision=2, expected_edit_token=linked["edit_token"], now=later + timedelta(hours=2))
    assert unlinked["expires_at"] == a["expires_at"]

    # Reapprove B -> A before expiry; even this cannot extend the deadline.
    linked_again = facts.save_case_declaration(B, SCOPE,
        goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], expected_revision=3, expected_edit_token=unlinked["edit_token"],
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
            expected_revision=linked_again["revision"], expected_edit_token=linked_again["edit_token"], now=after_lapse)


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
        depends_on_work_item_refs=[d], expected_revision=1, expected_edit_token=first_a["edit_token"],
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
            depends_on_work_item_refs=[A], expected_revision=old_b["revision"], expected_edit_token=old_b["edit_token"],
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
        save(B, expected_revision=old_b["revision"], expected_edit_token=old_b["edit_token"], depends_on_work_item_refs=[A])


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
        save(B, expected_revision=old_b["revision"], expected_edit_token=old_b["edit_token"])


def test_prerequisite_revision_erases_transitive_owner_approvals_not_unrelated(local):
    # B -> A was approved for A revision 1; changing A's declared goal
    # cannot silently carry B (or C -> B) into A revision 2.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    a = save()
    old_b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)

    revised = facts.save_case_declaration(
        A, SCOPE, goal_code="verify_operation",
        expected_outcome_code="operation_receipt_reviewed",
        expected_revision=a["revision"], expected_edit_token=a["edit_token"], now=NOW + timedelta(minutes=1))
    assert revised["revision"] == 2
    assert revised["goal_code"] == "verify_operation"
    assert facts.list_case_declarations(SCOPE, now=NOW + timedelta(minutes=1))["items"] == [
        revised, independent
    ]
    # Review requests are not erased, but old dependent declarations are.
    reviewed_refs = {
        item["work_item_ref"] for item in reviews.list_review_intentions(now=NOW)["items"]
    }
    assert {A, B, C, d}.issubset(reviewed_refs)
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            B, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            depends_on_work_item_refs=[A], expected_revision=old_b["revision"], expected_edit_token=old_b["edit_token"],
            now=NOW + timedelta(minutes=2))
    fresh_b = facts.save_case_declaration(
        B, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], now=NOW + timedelta(minutes=2))
    assert fresh_b["revision"] == 1
    assert fresh_b["depends_on_work_item_refs"] == [A]


def test_failed_prerequisite_cas_rolls_back_and_same_value_revision_invalidates(local):
    reviewed(A)
    reviewed(B)
    a = save()
    b = save(B, depends_on_work_item_refs=[A])
    # Invalid A -> B would create a cycle and must not erase B.
    with pytest.raises(facts.CaseDeclarationError, match="cyclic"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="verify_operation",
            expected_outcome_code="operation_receipt_reviewed",
            depends_on_work_item_refs=[B], expected_revision=1, expected_edit_token=a["edit_token"],
            now=NOW + timedelta(minutes=1))
    assert {item["work_item_ref"] for item in
            facts.list_case_declarations(SCOPE, now=NOW)["items"]} == {A, B}
    # Even a same-value CAS advances A's exact revision. Without pinned
    # dependency versions, B must be explicitly re-declared.
    same_value = facts.save_case_declaration(
        A, SCOPE, goal_code=a["goal_code"],
        expected_outcome_code=a["expected_outcome_code"],
        expected_revision=1, expected_edit_token=a["edit_token"], now=NOW + timedelta(minutes=1))
    assert same_value["revision"] == 2
    assert facts.list_case_declarations(SCOPE, now=NOW + timedelta(minutes=1))["items"] == [same_value]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            B, SCOPE, goal_code=b["goal_code"],
            expected_outcome_code=b["expected_outcome_code"],
            depends_on_work_item_refs=[A], expected_revision=b["revision"], expected_edit_token=b["edit_token"],
            now=NOW + timedelta(minutes=2))


def test_edit_requires_incarnation_token_and_preserves_it_on_cas(local):
    reviewed(A)
    first = save()
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(expected_revision=first["revision"])
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(expected_revision=first["revision"], expected_edit_token="0" * 32)
    updated = save(expected_revision=first["revision"],
                   expected_edit_token=first["edit_token"])
    assert updated["revision"] == first["revision"] + 1
    assert updated["edit_token"] == first["edit_token"]


def test_prerequisite_edit_redeclaration_blocks_old_dependent_aba(local):
    # Independent repro #6518893543: B rev1 is erased after A changes,
    # and a newly approved B rev1 must not accept an old B rev1 edit.
    for ref in (A, B, C):
        reviewed(ref)
    a = save()
    old_b = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    facts.save_case_declaration(
        A, SCOPE, goal_code="verify_operation",
        expected_outcome_code="operation_receipt_reviewed",
        expected_revision=a["revision"], expected_edit_token=a["edit_token"],
        now=NOW + timedelta(minutes=1))
    fresh_b = facts.save_case_declaration(
        B, SCOPE, goal_code="assess_declared_policy",
        expected_outcome_code="source_state_rechecked",
        depends_on_work_item_refs=[A], now=NOW + timedelta(minutes=2))
    assert old_b["revision"] == fresh_b["revision"] == 1
    assert old_b["edit_token"] != fresh_b["edit_token"]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            B, SCOPE, goal_code="verify_operation",
            expected_outcome_code="operation_receipt_reviewed",
            depends_on_work_item_refs=[A],
            expected_revision=old_b["revision"],
            expected_edit_token=old_b["edit_token"],
            now=NOW + timedelta(minutes=3))
    current = facts.list_case_declarations(SCOPE, now=NOW + timedelta(minutes=3))
    assert next(i for i in current["items"] if i["work_item_ref"] == B) == fresh_b
    assert C not in {i["work_item_ref"] for i in current["items"]}


def test_explicit_review_delete_redeclare_blocks_old_incarnation_cas(local):
    reviewed(A)
    old = save()
    assert reviews.remove_review_intention(A, now=NOW)
    at = NOW + timedelta(minutes=1)
    reviewed(A, at=at)
    fresh = facts.save_case_declaration(
        A, SCOPE, goal_code="assess_declared_policy",
        expected_outcome_code="source_state_rechecked", now=at)
    assert fresh["revision"] == old["revision"] == 1
    assert fresh["edit_token"] != old["edit_token"]
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="verify_operation",
            expected_outcome_code="operation_receipt_reviewed",
            expected_revision=old["revision"],
            expected_edit_token=old["edit_token"],
            now=at + timedelta(minutes=1))
    assert facts.list_case_declarations(SCOPE, now=at + timedelta(minutes=1))["items"] == [fresh]


def test_existing_v1_store_migrates_without_reauthorizing_declarations(local):
    reviewed(A)
    path = facts._path()
    db = sqlite3.connect(path)
    try:
        db.execute("""CREATE TABLE declarations (
            work_item_ref TEXT PRIMARY KEY, scope_ref TEXT NOT NULL,
            goal_code TEXT NOT NULL, expected_outcome_code TEXT NOT NULL,
            dependencies_json TEXT NOT NULL, revision INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL)""")
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO declarations VALUES (?,?,?,?,?,?,?,?,?)",
                   (A, SCOPE, "review_source_state", "source_state_rechecked",
                    json.dumps([]), 1, NOW.isoformat(), NOW.isoformat(),
                    (NOW + timedelta(days=7)).isoformat()))
        db.commit()
    finally:
        db.close()
    if os.name == "posix":
        path.chmod(0o600)
    migrated = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert len(migrated) == 1
    assert migrated[0]["revision"] == 1
    assert len(migrated[0]["edit_token"]) == 32
    with facts._database(create=False) as upgraded:
        assert upgraded.execute("PRAGMA user_version").fetchone()[0] == 2
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(expected_revision=1)
    updated = save(expected_revision=1,
                   expected_edit_token=migrated[0]["edit_token"])
    assert updated["revision"] == 2


def test_as_of_read_never_displays_future_owner_approval_or_revision(local):
    # The review is already active: the declaration and its later CAS edit
    # must still not be represented as owner-approved at an earlier instant.
    admitted = NOW - timedelta(hours=2)
    reviewed(A, at=admitted)
    reviewed(B, at=admitted)
    independent = facts.save_case_declaration(
        B, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked",
        now=NOW - timedelta(hours=1))
    first = save()
    earlier = facts.list_case_declarations(SCOPE, now=NOW - timedelta(minutes=30))
    assert earlier["items"] == [independent]
    assert {item["work_item_ref"] for item in
            facts.list_case_declarations(SCOPE, now=NOW)["items"]} == {A, B}

    revised = facts.save_case_declaration(
        A, SCOPE, goal_code="verify_operation",
        expected_outcome_code="operation_receipt_reviewed",
        expected_revision=first["revision"],
        expected_edit_token=first["edit_token"], now=NOW + timedelta(hours=1))
    # There is no historical snapshot store. Suppress the revised A rather
    # than falsely display its future revision as approved at this time.
    mid = facts.list_case_declarations(SCOPE, now=NOW + timedelta(minutes=30))
    assert mid["items"] == [independent]
    latest = facts.list_case_declarations(SCOPE, now=NOW + timedelta(hours=1))
    assert {item["work_item_ref"] for item in latest["items"]} == {A, B}
    assert next(item for item in latest["items"] if item["work_item_ref"] == A) == revised


def test_cas_rejects_backdated_owner_revision_without_mutation(local):
    reviewed(A, at=NOW - timedelta(hours=2))
    first = save()
    with pytest.raises(facts.CaseDeclarationError, match="backdated"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="verify_operation",
            expected_outcome_code="operation_receipt_reviewed",
            expected_revision=first["revision"],
            expected_edit_token=first["edit_token"],
            now=NOW - timedelta(minutes=1))
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [first]

    revised = facts.save_case_declaration(
        A, SCOPE, goal_code="verify_operation",
        expected_outcome_code="operation_receipt_reviewed",
        expected_revision=first["revision"],
        expected_edit_token=first["edit_token"], now=NOW + timedelta(hours=1))
    with pytest.raises(facts.CaseDeclarationError, match="backdated"):
        facts.save_case_declaration(
            A, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            expected_revision=revised["revision"],
            expected_edit_token=revised["edit_token"],
            now=NOW + timedelta(minutes=30))
    assert facts.list_case_declarations(SCOPE, now=NOW + timedelta(hours=1))["items"] == [revised]


def test_dependent_approval_cannot_precede_prerequisite_or_revision(local):
    admitted = NOW - timedelta(hours=2)
    reviewed(A, at=admitted)
    reviewed(B, at=admitted)
    first = save()
    with pytest.raises(facts.CaseDeclarationError, match="newer than approval"):
        facts.save_case_declaration(
            B, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            depends_on_work_item_refs=[A], now=NOW - timedelta(minutes=1))
    assert {item["work_item_ref"] for item in
            facts.list_case_declarations(SCOPE, now=NOW)["items"]} == {A}

    facts.save_case_declaration(
        A, SCOPE, goal_code="verify_operation",
        expected_outcome_code="operation_receipt_reviewed",
        expected_revision=first["revision"],
        expected_edit_token=first["edit_token"], now=NOW + timedelta(hours=1))
    with pytest.raises(facts.CaseDeclarationError, match="newer than approval"):
        facts.save_case_declaration(
            B, SCOPE, goal_code="review_source_state",
            expected_outcome_code="source_state_rechecked",
            depends_on_work_item_refs=[A], now=NOW + timedelta(minutes=30))
    assert {item["work_item_ref"] for item in
            facts.list_case_declarations(SCOPE, now=NOW + timedelta(hours=1))["items"]} == {A}


@pytest.mark.parametrize("invalid_edges", [
    "{}", '""', "null", "42", '["not-an-opaque-ref"]',
    json.dumps([A, A]), "{",
])
def test_corrupt_persisted_dependency_shape_cannot_launder_approval(local, invalid_edges):
    # Simulate a damaged private auxiliary ledger; the normal writer never
    # emits these values. B -> A must not become an independent declaration.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    root = save()
    dependent = save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    with facts._database(create=False) as db:
        db.execute("UPDATE declarations SET dependencies_json=? WHERE work_item_ref=?",
                   (invalid_edges, B))

    # Reads do not repair storage, but must withhold B and its dependent C.
    visible = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert {item["work_item_ref"] for item in visible} == {A, d}
    assert root in visible and independent in visible

    # A later valid explicit edit prunes the corrupt row and its dependents
    # atomically; it does not silently strip B's previously declared edge.
    updated = save(d, expected_revision=independent["revision"],
                   expected_edit_token=independent["edit_token"])
    with facts._database(create=False) as db:
        stored = {ref for (ref,) in db.execute(
            "SELECT work_item_ref FROM declarations").fetchall()}
    assert stored == {A, d}
    assert updated["revision"] == independent["revision"] + 1
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(B, expected_revision=dependent["revision"],
             expected_edit_token=dependent["edit_token"])


def test_corrupt_persisted_dependency_cycle_is_not_owner_approval(local):
    # The writer refuses cycles; this represents a damaged local ledger.
    # A <- B <- C, then a corrupted A -> B closes a forbidden A <-> B cycle.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    save()
    save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    with facts._database(create=False) as db:
        db.execute("UPDATE declarations SET dependencies_json=? WHERE work_item_ref=?",
                   (json.dumps([B]), A))
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]

    save(d, expected_revision=independent["revision"],
         expected_edit_token=independent["edit_token"])
    with facts._database(create=False) as db:
        stored = {ref for (ref,) in db.execute(
            "SELECT work_item_ref FROM declarations").fetchall()}
    assert stored == {d}


def test_corrupt_auxiliary_dependencies_do_not_block_review_deletion(local):
    reviewed(A)
    reviewed(B)
    save()
    save(B, depends_on_work_item_refs=[A])
    with facts._database(create=False) as db:
        db.execute("UPDATE declarations SET dependencies_json=? WHERE work_item_ref=?",
                   ("{}", B))
    # The primary review must still be removable. A corrupt auxiliary graph
    # triggers the existing fail-closed whole-ledger erasure path.
    assert reviews.remove_review_intention(A, now=NOW) is True
    assert not facts._path().exists()
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []

@pytest.mark.parametrize(("column", "value"), [
    ("created_at", "not-a-date"),
    ("created_at", (NOW + timedelta(hours=1)).isoformat()),
    ("created_at", (NOW - timedelta(days=8)).isoformat()),
    ("updated_at", ""),
    ("updated_at", (NOW + timedelta(hours=1)).isoformat()),
    ("updated_at", (NOW - timedelta(seconds=1)).isoformat()),
    ("updated_at", "2026-10-30T09:00:00Z"),
    ("updated_at", "2026-10-30T10:00:00+01:00"),
    ("expires_at", "invalid"),
    ("expires_at", (NOW - timedelta(seconds=1)).isoformat()),
    ("expires_at", (NOW + timedelta(days=30)).isoformat()),
])
def test_corrupt_stored_approval_clock_withholds_dependent_chain_and_prunes(
    local, column, value,
):
    # Simulate damaged auxiliary SQLite state, never an authorized write.
    # A <- B <- C must not be laundered into approved work if A's stored
    # approval timestamp is malformed, backdated, future, or over-retained.
    d = "owg:w:" + "d" * 32
    for ref in (A, B, C, d):
        reviewed(ref)
    save()
    save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    with facts._database(create=False) as db:
        db.execute(f"UPDATE declarations SET {column}=? WHERE work_item_ref=?",
                   (value, A))

    # Read is non-mutating, fail-closed, and keeps unrelated reviewed work.
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]
    with facts._database(create=False) as db:
        assert db.execute("SELECT count(*) FROM declarations").fetchone()[0] == 4

    # A subsequent authorized write erases the invalid prerequisite and
    # dependent closure, without extending or re-approving anything.
    updated = save(d, expected_revision=independent["revision"],
                   expected_edit_token=independent["edit_token"])
    assert updated["revision"] == independent["revision"] + 1
    with facts._database(create=False) as db:
        assert db.execute("SELECT work_item_ref FROM declarations").fetchall() == [(d,)]
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [updated]


@pytest.mark.parametrize(("column", "bad_value"), [
    ("goal_code", "claim_customer_paid"),
    ("expected_outcome_code", "customer_outcome_verified"),
    ("revision", 0),
    ("revision", -1),
    ("revision", 2_147_483_648),
    ("revision", "1.5"),
    ("edit_token", ""),
    ("edit_token", "A" * 32),
    ("scope_ref", "owg:scope:invalid"),
    ("work_item_ref", "owg:w:invalid"),
])
def test_corrupt_stored_owner_metadata_suppresses_and_prunes_dependents(
    local, column, bad_value,
):
    # Synthetic auxiliary-ledger corruption must never launder unsupported
    # owner codes, versions, edit tokens or opaque identities into approval.
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
        db.execute(
            f"UPDATE declarations SET {column}=? WHERE work_item_ref=?",
            (bad_value, A),
        )

    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]
    fresh = save(e)  # Prunes A and its transitive dependent closure.
    remaining = facts.list_case_declarations(SCOPE, now=NOW)["items"]
    assert {row["work_item_ref"] for row in remaining} == {d, e}
    assert independent in remaining and fresh in remaining
    with facts._database(create=False) as db:
        assert db is not None
        assert db.execute("SELECT COUNT(*) FROM declarations").fetchone()[0] == 2
    with pytest.raises(facts.CaseDeclarationError, match="stale"):
        save(B, expected_revision=old_b["revision"],
             expected_edit_token=old_b["edit_token"],
             depends_on_work_item_refs=[A])

@pytest.mark.parametrize("review_change", ["revoked", "replaced"])
def test_list_rechecks_review_window_after_sql_fetch(local, monkeypatch, review_change):
    # Deterministically model review revocation/replacement after the bounded
    # SQLite SELECT but before the owner-approval projection is returned.
    d = "owg:w:" + "d" * 32
    admitted = NOW - timedelta(hours=1)
    for ref in (A, B, C, d):
        reviewed(ref, at=admitted)
    save()
    save(B, depends_on_work_item_refs=[A])
    save(C, depends_on_work_item_refs=[B])
    independent = save(d)
    initial = facts._reviewed(NOW)
    calls = 0

    def review_changes_between_snapshots(at):
        nonlocal calls
        calls += 1
        if calls == 1:
            return initial
        live = dict(initial)
        if review_change == "revoked":
            live.pop(A)
        else:
            # A fresh review does not reapprove the old A -> B -> C chain.
            live[A] = (NOW, NOW + timedelta(days=7))
        return live

    monkeypatch.setattr(facts, "_reviewed", review_changes_between_snapshots)
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == [independent]
    assert calls == 2
    # A read suppresses stale facts; it does not rewrite the ledger.
    with facts._database(create=False) as db:
        assert db.execute("SELECT COUNT(*) FROM declarations").fetchone()[0] == 4


def test_list_rechecks_shortened_retention_after_sql_fetch(local, monkeypatch):
    # A was valid under the original 7-day policy but not a concurrent
    # reduction to one day. B is recent and must remain available.
    early = NOW - timedelta(days=2)
    reviewed(A, at=early)
    facts.save_case_declaration(
        A, SCOPE, goal_code="review_source_state",
        expected_outcome_code="source_state_rechecked", now=early)
    reviewed(B)
    save(B)
    calls = 0

    def shortened_retention():
        nonlocal calls
        calls += 1
        return 7 if calls == 1 else 1

    monkeypatch.setattr(facts, "_retention_days", shortened_retention)
    # Both rows were approved with seven-day deadlines. Under the stricter
    # one-day policy even the recent row's stored deadline is over-retained,
    # so neither approval may be presented or silently renewed.
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []
    assert calls == 2
    with facts._database(create=False) as db:
        assert db.execute("SELECT COUNT(*) FROM declarations").fetchone()[0] == 2


def test_list_scrubs_when_retention_revoked_after_sql_fetch(local, monkeypatch):
    reviewed(A)
    save()
    assert facts._path().exists()
    calls = 0

    def revoked_retention():
        nonlocal calls
        calls += 1
        return 7 if calls == 1 else None

    monkeypatch.setattr(facts, "_retention_days", revoked_retention)
    assert facts.list_case_declarations(SCOPE, now=NOW)["items"] == []
    assert calls == 2
    assert not facts._path().exists()


@pytest.mark.parametrize("competing_change", ["delete", "revise"])
def test_save_returns_exact_transaction_snapshot_under_post_commit_race(
    local, monkeypatch, competing_change,
):
    """A second writer after COMMIT cannot rewrite the first writer's response.

    Intercept only the successful COMMIT boundary; the contender uses a
    separate real SQLite connection, not a mocked _get or fabricated row.
    The returned revision/token must belong to the first transaction even
    when the stored row has already been deleted or revised.
    """
    reviewed(A)
    first = save() if competing_change == "revise" else None
    real_database = facts._database
    commits = []

    class PostCommitContender:
        def __init__(self, db):
            self.db = db

        def execute(self, statement, *args):
            result = self.db.execute(statement, *args)
            if statement == "COMMIT":
                commits.append(True)
                with sqlite3.connect(facts._path()) as rival:
                    if competing_change == "delete":
                        rival.execute(
                            "DELETE FROM declarations WHERE work_item_ref=?", (A,))
                    else:
                        rival.execute(
                            "UPDATE declarations SET revision=revision+1,"
                            "goal_code=? WHERE work_item_ref=?",
                            ("verify_operation", A))
            return result

        def __getattr__(self, name):
            return getattr(self.db, name)

    @contextmanager
    def racing_database(*, create):
        with real_database(create=create) as db:
            yield PostCommitContender(db) if db is not None else None

    monkeypatch.setattr(facts, "_database", racing_database)
    if first is None:
        saved = save()
        assert saved["revision"] == 1
        assert saved["goal_code"] == "review_source_state"
    else:
        saved = facts.save_case_declaration(
            A, SCOPE, goal_code="assess_declared_policy",
            expected_outcome_code="human_review_recorded",
            expected_revision=first["revision"],
            expected_edit_token=first["edit_token"],
            now=NOW + timedelta(minutes=1))
        assert saved["revision"] == 2
        assert saved["goal_code"] == "assess_declared_policy"
        assert saved["edit_token"] == first["edit_token"]
    assert len(commits) == 1

    monkeypatch.setattr(facts, "_database", real_database)
    stored = facts.list_case_declarations(
        SCOPE, now=NOW + timedelta(minutes=1))["items"]
    if competing_change == "delete":
        assert stored == []
    else:
        assert len(stored) == 1
        assert stored[0]["revision"] == 3
        assert stored[0]["goal_code"] == "verify_operation"
        assert stored[0]["revision"] != saved["revision"]
