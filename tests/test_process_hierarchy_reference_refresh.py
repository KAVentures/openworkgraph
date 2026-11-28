"""Exact R12 batch reference preview: retained map store, role and drift tests."""
from copy import deepcopy
import json

import pytest
from pydantic import ValidationError

from gateway.process_hierarchies import ProcessHierarchies
from gateway.process_hierarchy_reference_refresh import RefreshRequest, review
from gateway.process_collaboration import (
    ProcessArchiveWrite, ProcessDeleteWrite, ProcessCollaborationError,
)
from test_process_collaboration import store, person, write, grant, review as model_review
from test_process_hierarchies import process, pin, hierarchy, save_body


def fixture(store):
    root, a, b, reused, leaf = [process(store, title) for title in
                              ("Root", "A", "B", "Reused", "Leaf")]
    h = hierarchy(root, a, b)
    h["nodes"] += [
        {"id": "shared_a", "parent_id": "n1", "process": pin(reused)},
        {"id": "shared_b", "parent_id": "n2", "process": pin(reused)},
        {"id": "leaf", "parent_id": "shared_a", "process": pin(leaf)},
    ]
    maps = ProcessHierarchies(store)
    made = maps.save(person(), root["workflow_id"], save_body(h))
    return maps, root, a, b, reused, leaf, made


def request(made, *nodes, expected_report_sha256=None):
    return RefreshRequest(map_revision=made["revision"], expected_map_sha256=made["content_sha256"],
                          node_ids=list(nodes), expected_report_sha256=expected_report_sha256)


def update(store, record, title):
    return store.save(person(), write(title, record["revision"]), record["workflow_id"])["process"]


def test_partial_reuse_refresh_preserves_other_occurrences_and_all_structure(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    new = update(store, reused, "Reused v2")
    wid = root["workflow_id"]
    result = review(maps, person(), wid, request(made, "shared_a"))
    assert result["can_stage"] and not result["persisted"]
    assert result["changed_node_ids"] == ["shared_a"]
    assert set(result["affected_node_ids"]) == {"root", "n1", "shared_a", "leaf"}
    assert result["branch_impacts"] == [{"node_id": "shared_a", "ancestor_node_ids": ["root", "n1"],
                                        "descendant_node_ids": ["leaf"], "descendant_count": 1}]
    reuse = result["reused_processes"][0]
    assert reuse["occurrence_node_ids"] == ["shared_a", "shared_b"]
    assert reuse["distinct_pins_before"] == 1 and reuse["distinct_pins_after"] == 2
    assert reuse["mixed_pins_after"]
    assert "reused_process_has_multiple_proposed_pins" in result["warnings"]
    assert "unselected_outdated_references_remain" in result["warnings"]
    assert "changed_reference_lacks_current_independent_design_review" in result["warnings"]
    original = maps.detail(person(), wid)["artifact"]["hierarchy"]
    candidate = deepcopy(original)
    candidate["nodes"][3]["process"] = pin(new)
    assert result["candidate_hierarchy"] == candidate
    assert maps.detail(person(), wid)["latest_revision"] == 1
    assert not result["model_reviews_propagated"] and not result["execution_authorized"]
    assert not result["benefit_rollup_included"]


def test_all_reused_occurrences_refresh_deterministically_and_save_is_separate(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    update(store, reused, "Reused v2")
    wid = root["workflow_id"]
    one = review(maps, person(), wid, request(made, "shared_a", "shared_b"))
    two = review(maps, person(), wid, request(made, "shared_b", "shared_a",
                 expected_report_sha256=one["report_sha256"]))
    assert one == two
    assert one["changed_node_ids"] == ["shared_a", "shared_b"]
    assert one["counts"]["affected_occurrences"] == 6
    assert one["counts"]["mixed_pin_workflows_after"] == 0
    assert maps.detail(person(), wid)["latest_revision"] == 1
    saved = maps.save(person(), wid, save_body(one["candidate_hierarchy"], 1))
    assert saved["revision"] == 2
    assert maps.detail(person(), wid)["artifact"]["hierarchy"] == one["candidate_hierarchy"]
    assert maps.detail(person(), wid, 1)["artifact"]["content_sha256"] == made["content_sha256"]
    with pytest.raises(ProcessCollaborationError, match="basis changed"):
        review(maps, person(), wid, request(made, "shared_a", "shared_b",
               expected_report_sha256=one["report_sha256"]))


def test_root_staleness_requires_explicit_root_refresh(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    new_root = update(store, root, "Root v2")
    update(store, a, "A v2")
    wid = root["workflow_id"]
    result = review(maps, person(), wid, request(made, "n1"))
    assert not result["can_stage"]
    assert {"node_id": "root", "reason": "root_latest_reference_required"} in result["blockers"]
    fixed = review(maps, person(), wid, request(made, "root", "n1"))
    assert fixed["can_stage"]
    assert fixed["candidate_hierarchy"]["nodes"][0]["process"] == pin(new_root)
    assert set(fixed["affected_node_ids"]) == {"root", "n1", "n2", "shared_a", "shared_b", "leaf"}


def test_archived_and_deleted_unselected_references_block_stage_without_hidden_fields(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    update(store, a, "A v2")
    store.archive(person(), b["workflow_id"],
                  ProcessArchiveWrite(expected_access_revision=1, archived=True, user_confirmed=True))
    store.delete(person(), leaf["workflow_id"],
                 ProcessDeleteWrite(expected_revision=1, expected_access_revision=1, user_confirmed=True))
    wid = root["workflow_id"]
    result = review(maps, person(), wid, request(made, "n1"))
    assert not result["can_stage"]
    assert {"node_id": "n2", "reason": "retained_reference_archived"} in result["blockers"]
    assert {"node_id": "leaf", "reason": "retained_reference_unavailable"} in result["blockers"]
    gone = next(r for r in result["rows"] if r["node_id"] == "leaf")
    assert gone["latest"] is None and gone["before_review"] is None
    assert "Leaf" not in json.dumps(gone)
    selected = review(maps, person(), wid, request(made, "n1", "n2", "leaf"))
    assert not selected["can_stage"]
    assert {"node_id": "leaf", "reason": "selected_latest_unavailable"} in selected["blockers"]


def test_review_changes_invalidate_pin_without_granting_review(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    newest = update(store, a, "A v2")
    wid = root["workflow_id"]
    first = review(maps, person(), wid, request(made, "n1"))
    grant(store, a["workflow_id"], members=[{"actor_id": "bob", "role": "reviewer"}])
    store.review(person("bob"), a["workflow_id"], model_review(newest))
    with pytest.raises(ProcessCollaborationError, match="basis changed"):
        review(maps, person(), wid, request(made, "n1", expected_report_sha256=first["report_sha256"]))
    fresh = review(maps, person(), wid, request(made, "n1"))
    assert fresh["can_stage"] and not fresh["model_reviews_propagated"]
    assert next(r for r in fresh["rows"] if r["node_id"] == "n1")["proposed_review"] == "reviewed_design"
    assert "changed_reference_lacks_current_independent_design_review" not in fresh["warnings"]


def test_concurrent_new_child_revision_and_revoked_editor_change_basis(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    new = update(store, a, "A v2")
    wid = root["workflow_id"]
    grant(store, wid, members=[{"actor_id": "bob", "role": "editor"}])
    r = review(maps, person("bob"), wid, request(made, "n1"))
    assert r["can_stage"]
    update(store, new, "A v3")
    with pytest.raises(ProcessCollaborationError, match="basis changed"):
        review(maps, person("bob"), wid, request(made, "n1", expected_report_sha256=r["report_sha256"]))
    r = review(maps, person("bob"), wid, request(made, "n1"))
    grant(store, wid, members=[], expected=2)
    with pytest.raises(ProcessCollaborationError, match="basis changed"):
        review(maps, person("bob"), wid, request(made, "n1", expected_report_sha256=r["report_sha256"]))
    denied = review(maps, person("bob"), wid, request(made, "n1"))
    assert not denied["can_stage"]
    assert {"node_id": "root", "reason": "current_root_editor_required"} in denied["blockers"]


def test_invalid_scope_digest_nodes_and_historical_preview(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    wid = root["workflow_id"]
    for p in (person(org="foreign"), person(scopes=frozenset())):
        with pytest.raises(ProcessCollaborationError):
            review(maps, p, wid, request(made, "root"))
    with pytest.raises(ProcessCollaborationError, match="digest changed"):
        review(maps, person(), wid, request({**made, "content_sha256": "0"*64}, "root"))
    with pytest.raises(ProcessCollaborationError, match="exact node IDs"):
        review(maps, person(), wid, request(made, "not_a_node"))
    unchanged = review(maps, person(), wid, request(made, "root"))
    assert not unchanged["can_stage"] and "no_reference_changes" in unchanged["warnings"]
    h = maps.detail(person(), wid)["artifact"]["hierarchy"]
    h["category"] = "New category"
    maps.save(person(), wid, save_body(h, 1))
    historical = review(maps, person(), wid, request(made, "root"))
    assert not historical["can_stage"]
    assert {"node_id": "root", "reason": "historical_map_is_read_only"} in historical["blockers"]


def test_audit_has_counts_and_pins_not_titles_or_candidate_models(store):
    maps, root, a, b, reused, leaf, made = fixture(store)
    update(store, a, "Confidential title")
    report = review(maps, person(), root["workflow_id"], request(made, "n1"))
    with store.db.connect() as c:
        audit = [json.loads(row[0]) for row in store.db._execute(
            c, "SELECT details_json FROM audit_log WHERE action=?",
            ("process.hierarchy.reference_refresh_reviewed",)).fetchall()]
    assert len(audit) == 1
    assert audit[0]["report_sha256"] == report["report_sha256"]
    assert audit[0]["changed_count"] == 1
    assert "Confidential title" not in json.dumps(audit)
    assert "candidate_hierarchy" not in audit[0]


@pytest.mark.parametrize("changes", [
    {"node_ids": []}, {"node_ids": ["root", "root"]}, {"node_ids": ["bad/path"]},
    {"map_revision": True}, {"map_revision": 21}, {"expected_map_sha256": "bad"},
    {"expected_report_sha256": "bad"}, {"extra": True},
])
def test_strict_request_validation(changes):
    body = {"map_revision": 1, "expected_map_sha256": "a"*64, "node_ids": ["root"]}
    with pytest.raises(ValidationError):
        RefreshRequest.model_validate({**body, **changes})
