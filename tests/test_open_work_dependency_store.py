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
