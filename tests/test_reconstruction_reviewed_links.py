"""Integration tests for the actual production linker and reviewed overlay."""
import copy
import pytest

from server.reconstruction_reviewed_links import review_candidates


def event(event_id, second, ref=None, tab="shared", actor="actor"):
    metadata = {"tab_context_id": tab}
    if ref:
        metadata["resource_reference"] = {
            "resource_ref": ref, "provider": "jira",
            "resource_kind": "issue", "host": "jira.example"
        }
    return {
        "event_id": event_id,
        "observed_at": f"2026-11-01T10:00:{second:02d}Z",
        "app": "Browser", "event_type": "click", "source": "browser",
        "organization_id": "tenant", "actor_id": actor,
        "device_id": "device", "session_id": "session",
        "metadata": metadata,
    }


def accept(manifest, candidate):
    return {"version": 1, "evidence_sha256": manifest["evidence_sha256"],
            "accepted": [candidate]}


def test_explicit_review_joins_only_unreferenced_same_scope_candidates():
    events = [event("a", 0, tab="loose"), event("b", 1, tab="loose")]
    manifest = review_candidates(events)
    assert manifest["candidate_count"] >= 1
    result = review_candidates(events, accept(manifest, manifest["candidates"][0]))
    assert result["reviewed_edge_count"] == 1
    assert any(set(group) == {"a", "b"} for group in result["proposed_groups"])
    assert result["canonical_membership_unchanged"] is True
    assert len(review_candidates(events, {"version": 1,
        "evidence_sha256": manifest["evidence_sha256"], "accepted": []})["proposed_groups"]) == 2


def test_stale_review_rejected_on_source_change_and_revocation():
    events = [event("a", 0, tab="loose"), event("b", 1, tab="loose")]
    manifest = review_candidates(events)
    decision = accept(manifest, manifest["candidates"][0])
    with pytest.raises(ValueError, match="stale"):
        review_candidates(events[:1], decision)
    changed = copy.deepcopy(events)
    changed[1]["observed_at"] = "2026-11-01T10:00:04Z"
    with pytest.raises(ValueError, match="stale"):
        review_candidates(changed, decision)


def test_unknown_and_duplicate_decisions_refused():
    events = [event("a", 0, tab="loose"), event("b", 1, tab="loose")]
    manifest = review_candidates(events)
    bad = accept(manifest, {"event_id": "a", "toward_event_id": "missing",
                            "relation": "same_tab_unreferenced"})
    with pytest.raises(ValueError, match="unknown"):
        review_candidates(events, bad)
    candidate = manifest["candidates"][0]
    bad = {"version": 1, "evidence_sha256": manifest["evidence_sha256"],
           "accepted": [candidate, candidate]}
    with pytest.raises(ValueError, match="duplicate"):
        review_candidates(events, bad)


def test_no_cross_actor_join_even_if_tab_ids_reused():
    events = [event("a", 0, tab="shared", actor="alice"),
              event("b", 1, tab="shared", actor="bob")]
    manifest = review_candidates(events)
    assert not manifest["candidates"]


def test_no_merge_of_distinct_record_claims_through_loose_bridge():
    events = [event("a", 0, ref="issue-a"),
              event("loose", 1), event("b", 2, ref="issue-b")]
    manifest = review_candidates(events)
    assert manifest["candidate_count"] >= 1
    decisions = {"version": 1, "evidence_sha256": manifest["evidence_sha256"],
                 "accepted": manifest["candidates"]}
    result = review_candidates(events, decisions)
    assert not any({"a", "b"} <= set(group) for group in result["proposed_groups"])


def test_rejects_malformed_review_and_bounds():
    with pytest.raises(ValueError, match="bounded"):
        review_candidates([{}] * 10001)
    with pytest.raises(ValueError, match="version"):
        review_candidates([], {"version": 2})
