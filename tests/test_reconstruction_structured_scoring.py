"""Production-path tests for the conservative structural corroboration scorer."""
import pytest

from server.reconstruction_structured_scoring import corroborated_assignment


def test_consent_is_mandatory():
    with pytest.raises(ValueError, match="consent"):
        corroborated_assignment([])


def test_empty_observations_produce_no_authoritative_links():
    result = corroborated_assignment([], consent=True)
    assert result["proposals"] == []
    assert result["authoritative"] is False
    assert result["canonical_membership_unchanged"] is True
    assert result["semantic_model_used"] is False


def test_weak_single_signal_does_not_become_semantic_proof():
    events = [
        {"event_id": "one", "organization_id": "org", "actor_id": "actor",
         "device_id": "device", "session_id": "session", "app": "browser",
         "tab_id": "tab"},
        {"event_id": "two", "organization_id": "org", "actor_id": "actor",
         "device_id": "device", "session_id": "session", "app": "browser",
         "tab_id": "tab"},
    ]
    result = corroborated_assignment(events, consent=True)
    assert result["proposals"] == []
    assert result["semantic_model_used"] is False


def test_bounded_input_refuses_invalid_shape():
    with pytest.raises(ValueError, match="bounded"):
        corroborated_assignment({}, consent=True)


def test_independent_signals_on_different_members_of_same_components(monkeypatch):
    """A real production component can have multiple candidate endpoints."""
    from server import reconstruction_structured_scoring as scorer

    queue = {
        "evidence_sha256": "digest", "truncated": False,
        "queue": [
            {"event_id": "a1", "toward_event_id": "b1",
             "relation": "same_tab_unreferenced", "risk_reasons": []},
            {"event_id": "a2", "toward_event_id": "b2",
             "relation": "surface_continuity", "risk_reasons": []},
        ],
    }
    monkeypatch.setattr(scorer, "prioritized_review_queue",
                        lambda events, limit: queue)
    monkeypatch.setattr(scorer, "link_observations",
                        lambda events: {"members": {"a1": ["a1", "a2"],
                                                     "b1": ["b1", "b2"]}})
    captured = {}

    def assignment(events, manifest, *, consent):
        captured.update(manifest)
        return {"proposals": [], "authoritative": False}

    monkeypatch.setattr(scorer, "semantic_assignment", assignment)
    result = scorer.corroborated_assignment([], consent=True)
    assert result["corroborated_pairs"] == 1
    assert captured["pairs"] == [
        {"event_id": "a1", "toward_event_id": "b1", "score": 0.84}
    ]


def test_repeated_family_across_component_members_does_not_corrobate(monkeypatch):
    from server import reconstruction_structured_scoring as scorer
    monkeypatch.setattr(scorer, "prioritized_review_queue",
                        lambda events, limit: {
                            "evidence_sha256": "digest", "truncated": False,
                            "queue": [
                                {"event_id": "a1", "toward_event_id": "b1",
                                 "relation": "same_tab_unreferenced",
                                 "risk_reasons": []},
                                {"event_id": "a2", "toward_event_id": "b2",
                                 "relation": "same_tab_multiplexed",
                                 "risk_reasons": []},
                            ],
                        })
    monkeypatch.setattr(scorer, "link_observations",
                        lambda events: {"members": {"a1": ["a1", "a2"],
                                                     "b1": ["b1", "b2"]}})
    captured = {}
    monkeypatch.setattr(scorer, "semantic_assignment",
                        lambda events, manifest, *, consent:
                        captured.update(manifest) or {"proposals": []})
    result = scorer.corroborated_assignment([], consent=True)
    assert result["corroborated_pairs"] == 0
    assert captured["pairs"] == []
