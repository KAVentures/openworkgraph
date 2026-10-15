"""Record-linked activity is evidence navigation, never inferred business truth."""
from __future__ import annotations

from server.record_evidence import record_linked_activity

REF = "owg:r:" + "a" * 32


def _event(n: int, *, session: str = "s1", actor: str = "person-a",
           ref: str = REF, provider: str = "jira") -> dict:
    return {
        "event_id": f"ev-{n}", "session_id": session, "actor_id": actor,
        "observed_at": f"2026-10-15T10:00:{n:02d}Z",
        "metadata": {"resource_reference": {
            "resource_ref": ref, "provider": provider, "resource_kind": "issue",
        }},
    }


def test_record_group_connects_observations_only_by_identical_opaque_ref():
    projection = record_linked_activity([
        _event(1), _event(2, session="s2", actor="person-b"),
        _event(3, session="s3"),
    ])
    assert projection["total_groups"] == 1
    group = projection["groups"][0]
    assert group["resource_ref"] == REF
    assert group["observed_session_count"] == 3
    assert group["observed_actor_count"] == 2
    assert group["observed_event_count"] == 3
    assert group["handoff_or_outcome_verified"] is False
    assert group["cross_system_identity_inferred"] is False
    assert "source_system_ids_or_values_included" in projection


def test_unapproved_or_mismatched_refs_fail_closed():
    # An unnormalized business ID must not be used for a case join.
    bad = record_linked_activity([_event(1, ref="ticket-123"),
                                  _event(2, session="s2", ref="ticket-123")])
    assert bad["total_groups"] == 0
    assert bad["observed_events_with_normalized_reference"] == 0
    # Contradictory provider data for the same opaque token is ambiguous.
    mixed = record_linked_activity([_event(1), _event(2, provider="github")])
    assert mixed["groups"] == []


def test_no_opting_in_to_references_never_fabricates_case_linkage():
    events = [
        {"event_id": "e1", "session_id": "s1", "metadata": {}},
        {"event_id": "e2", "session_id": "s2", "metadata": {}},
    ]
    projection = record_linked_activity(events)
    assert projection["groups"] == []
    assert projection["references_are_opt_in"] is True
    assert projection["no_group_means_no_matching_observed_reference_not_no_related_work"] is True


def test_existing_candidate_discovery_includes_record_view_as_additive_field(monkeypatch):
    from server import workflow_evidence
    monkeypatch.setattr(workflow_evidence, "derive_executions", lambda _raw: [])
    data = workflow_evidence.list_workflow_evidence_candidates(
        _raw_events=[_event(1), _event(2, session="s2")],
    )
    assert data["candidate_clusters"] == []
    assert data["record_linked_activity"]["groups"][0]["resource_ref"] == REF
    assert data["returned_clusters_are_verified_tasks"] is False
