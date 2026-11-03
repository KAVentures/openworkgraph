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
