"""Executable local SQLite acceptance for declared dependency graph."""
import pytest

from server.open_work_dependency_store import (
    declare_dependency, dependency_context, remove_dependency, purge_actor,
)


def test_durable_transitive_dependencies_and_cycle_rejection(tmp_path):
    path = str(tmp_path / "graph.sqlite")
    kwargs = {"path": path, "scope": "tenant1", "actor": "alice"}
    assert declare_dependency(**kwargs, case_id="invoice", prerequisite="approval")
    assert declare_dependency(**kwargs, case_id="approval", prerequisite="identity")
    assert not declare_dependency(**kwargs, case_id="invoice", prerequisite="approval")
    context = dependency_context(**kwargs, case_id="invoice")
    assert context == {
        "case_id": "invoice", "direct": ["approval"],
        "prerequisites": ["approval", "identity"], "truncated": False,
        "source_status": "unknown", "customer_outcome": "unknown",
    }
    with pytest.raises(ValueError, match="cycle"):
        declare_dependency(**kwargs, case_id="identity", prerequisite="invoice")
    assert dependency_context(**kwargs, case_id="invoice") == context


def test_actor_tenant_isolation_and_revocation(tmp_path):
    path = str(tmp_path / "graph.sqlite")
    for scope, actor in (("one", "alice"), ("one", "bob"), ("two", "alice")):
        assert declare_dependency(path, scope=scope, actor=actor,
                                  case_id="case", prerequisite="source")
    assert purge_actor(path, scope="one", actor="alice") == 1
    assert dependency_context(path, scope="one", actor="alice", case_id="case")["direct"] == []
    assert dependency_context(path, scope="one", actor="bob", case_id="case")["direct"] == ["source"]
    assert dependency_context(path, scope="two", actor="alice", case_id="case")["direct"] == ["source"]
    assert remove_dependency(path, scope="one", actor="bob", case_id="case", prerequisite="source")
    assert not remove_dependency(path, scope="one", actor="bob", case_id="case", prerequisite="source")


def test_bounded_traversal_and_bad_scopes(tmp_path):
    path = str(tmp_path / "graph.sqlite")
    for i in range(5):
        declare_dependency(path, scope="one", actor="alice",
                           case_id=f"case{i}", prerequisite=f"case{i+1}")
    result = dependency_context(path, scope="one", actor="alice", case_id="case0", limit=2)
    assert result["truncated"] is True
    assert len(result["prerequisites"]) == 2
    with pytest.raises(ValueError):
        declare_dependency(path, scope="../other", actor="alice",
                           case_id="case", prerequisite="other")
    with pytest.raises(ValueError):
        dependency_context(path, scope="one", actor="alice", case_id="case0", limit=0)
    with pytest.raises(ValueError):
        declare_dependency(path, scope="one", actor="alice",
                           case_id="same", prerequisite="same")

def test_reviewed_snapshot_atomic_reconciliation_and_scope_isolation(tmp_path):
    from server.open_work_dependency_store import replace_reviewed_dependencies
    from server.open_work_case_declarations import CaseDeclarationError
    path = str(tmp_path / "reviewed.sqlite")
    kwargs = {"path": path, "scope": "tenant1", "actor": "alice"}
    ref1 = "owg:w:" + "a" * 32
    ref2 = "owg:w:" + "b" * 32
    def row(ref, deps):
        return {
            "work_item_ref": ref, "depends_on_work_item_refs": deps,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "revision": 1, "updated_at": "2026-11-07T00:00:00Z",
            "expires_at": "2026-11-08T00:00:00Z",
            "provenance": "explicit_owner_declaration_not_source_verified",
            "expected_outcome_verified": False,
            "business_obligation_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
            "source_state": "unknown_until_explicit_recheck",
        }
    snapshot = {"format": "openworkgraph.local-case-declarations.v1",
                "items": [row(ref1, [ref2]), row(ref2, [])]}
    declare_dependency(path, scope="tenant1", actor="bob",
                       case_id="other", prerequisite="unrelated")
    assert replace_reviewed_dependencies(**kwargs, snapshot=snapshot)["added_reviewed_edges"] == 1
    assert dependency_context(**kwargs, case_id=ref1)["direct"] == [ref2]
    assert replace_reviewed_dependencies(**kwargs, snapshot=snapshot)["added_reviewed_edges"] == 0
    invalid = {"format": snapshot["format"], "items": [row(ref1, ["missing"])]}
    with pytest.raises(CaseDeclarationError):
        replace_reviewed_dependencies(**kwargs, snapshot=invalid)
    assert dependency_context(**kwargs, case_id=ref1)["direct"] == [ref2]
    snapshot["items"][0]["depends_on_work_item_refs"] = []
    assert replace_reviewed_dependencies(**kwargs, snapshot=snapshot)["removed_stale_edges"] == 1
    assert dependency_context(**kwargs, case_id=ref1)["direct"] == []
    assert dependency_context(path, scope="tenant1", actor="bob", case_id="other")["direct"] == ["unrelated"]


def test_dependency_store_refuses_unsafe_existing_paths(tmp_path):
    import os
    import stat
    path = tmp_path / "dependencies.sqlite"
    kwargs = {"path": str(path), "scope": "tenant", "actor": "alice",
              "case_id": "invoice", "prerequisite": "approval"}
    assert declare_dependency(**kwargs)
    if os.name == "posix":
        os.chmod(path, 0o644)
        with pytest.raises(ValueError, match="permissions"):
            declare_dependency(**kwargs)
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
        os.chmod(path, 0o600)
        alias = tmp_path / "hardlink.sqlite"
        os.link(path, alias)
        with pytest.raises(ValueError, match="unsafe"):
            dependency_context(str(path), scope="tenant", actor="alice", case_id="invoice")
        alias.unlink()
    symlink = tmp_path / "symlink.sqlite"
    symlink.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        dependency_context(str(symlink), scope="tenant", actor="alice", case_id="invoice")
    sidecar = tmp_path / "dependencies.sqlite-wal"
    sidecar.write_bytes(b"unexpected")
    with pytest.raises(ValueError, match="sidecar"):
        declare_dependency(**kwargs)
    sidecar.unlink()
    assert dependency_context(str(path), scope="tenant", actor="alice",
                              case_id="invoice")["direct"] == ["approval"]


def test_live_review_reconciliation_and_revocation_fail_closed(tmp_path):
    from server.open_work_dependency_store import reconcile_current_reviewed_dependencies
    from server.open_work_case_declarations import CaseDeclarationError

    path = str(tmp_path / "live.sqlite")
    scope, actor = "owg:scope:" + "a" * 32, "alice"
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    def row(ref, deps):
        return {
            "work_item_ref": ref, "depends_on_work_item_refs": deps,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "revision": 1, "updated_at": "2026-11-07T00:00:00Z",
            "expires_at": "2026-11-08T00:00:00Z",
            "provenance": "explicit_owner_declaration_not_source_verified",
            "expected_outcome_verified": False,
            "business_obligation_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
            "source_state": "unknown_until_explicit_recheck",
        }
    snapshot = {"format": "openworkgraph.local-case-declarations.v1",
                "items": [row(a, [b]), row(b, [])]}
    live = {"scope": scope, "snapshot": snapshot}
    def scope_provider():
        return live["scope"]
    def snapshot_provider(_scope):
        return live["snapshot"]
    kwargs = dict(path=path, scope=scope, actor=actor,
                  scope_provider=scope_provider, snapshot_provider=snapshot_provider)
    result = reconcile_current_reviewed_dependencies(**kwargs)
    assert result["current_review_rechecked"] is True
    assert result["agent_execution_authorized"] is False
    assert dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == [b]
    live["snapshot"] = {"format": snapshot["format"], "items": []}
    assert reconcile_current_reviewed_dependencies(**kwargs)["removed_stale_edges"] == 1
    assert dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []
    live["snapshot"] = snapshot
    reconcile_current_reviewed_dependencies(**kwargs)
    live["scope"] = None
    with pytest.raises(CaseDeclarationError, match="scope"):
        reconcile_current_reviewed_dependencies(**kwargs)
    assert dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []


def test_live_review_change_during_reconciliation_purges_mirror(tmp_path):
    from server.open_work_dependency_store import reconcile_current_reviewed_dependencies
    from server.open_work_case_declarations import CaseDeclarationError

    path = str(tmp_path / "race.sqlite")
    scope, actor = "owg:scope:" + "c" * 32, "alice"
    a, b = "owg:w:" + "a" * 32, "owg:w:" + "b" * 32
    def row(ref, deps):
        return {
            "work_item_ref": ref, "depends_on_work_item_refs": deps,
            "goal_code": "verify_operation",
            "expected_outcome_code": "operation_receipt_reviewed",
            "revision": 1, "updated_at": "2026-11-07T00:00:00Z",
            "expires_at": "2026-11-08T00:00:00Z",
            "provenance": "explicit_owner_declaration_not_source_verified",
            "expected_outcome_verified": False,
            "business_obligation_verified": False,
            "customer_outcome_verified": False,
            "agent_execution_authorized": False,
            "source_state": "unknown_until_explicit_recheck",
        }
    original = {"format": "openworkgraph.local-case-declarations.v1",
                "items": [row(a, [b]), row(b, [])]}
    revoked = {"format": original["format"], "items": []}
    calls = iter((original, revoked))
    with pytest.raises(CaseDeclarationError, match="changed"):
        reconcile_current_reviewed_dependencies(
            path, scope=scope, actor=actor,
            scope_provider=lambda: scope,
            snapshot_provider=lambda _scope: next(calls))
    assert dependency_context(path, scope=scope, actor=actor, case_id=a)["direct"] == []
