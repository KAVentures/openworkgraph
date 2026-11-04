"""Interruption triage: production candidate integration and safety gates."""
import pytest

from server.reconstruction_interruption_triage import interruption_review


def _event(identifier, time):
    return {"event_id": identifier, "observed_at": time}


def _queue(*rows):
    return {"evidence_sha256": "a" * 64, "queue": list(rows), "truncated": False}


def _candidate(reasons=()):
    return {"event_id": "a", "toward_event_id": "b",
            "relation": "same_tab_unreferenced", "risk_reasons": list(reasons)}


def test_paused_candidate_is_review_only(monkeypatch):
    monkeypatch.setattr("server.reconstruction_interruption_triage.prioritized_review_queue",
                        lambda events, limit: _queue(_candidate()))
    events = [_event("a", "2026-01-01T12:00:00Z"),
              _event("b", "2026-01-01T12:05:00+00:00")]
    result = interruption_review(events, consent=True)
    assert result["proposals"][0]["interruption_class"] == "paused"
    assert result["proposals"][0]["elapsed_seconds"] == 300
    assert result["proposals"][0]["automatic_merge"] is False
    assert result["time_only_linking"] is False


@pytest.mark.parametrize("reasons", [
    ("missing_or_cross_scope",), ("tainted_source_evidence",),
    ("conflicting_explicit_records",),
])
def test_unsafe_candidates_never_proposed(monkeypatch, reasons):
    monkeypatch.setattr("server.reconstruction_interruption_triage.prioritized_review_queue",
                        lambda events, limit: _queue(_candidate(reasons)))
    assert not interruption_review([_event("a", "2026-01-01T00:00:00Z"),
                                    _event("b", "2026-01-01T00:01:00Z")],
                                   consent=True)["proposals"]


def test_competing_candidates_abstain(monkeypatch):
    monkeypatch.setattr("server.reconstruction_interruption_triage.prioritized_review_queue",
                        lambda events, limit: _queue(_candidate(("competing_candidate_components",))))
    result = interruption_review([_event("a", "2026-01-01T00:00:00Z"),
                                  _event("b", "2026-01-01T00:10:00Z")], consent=True)
    assert result["proposals"][0]["review_status"] == "abstain_competing"


def test_duplicate_conflicting_timestamp_withheld(monkeypatch):
    monkeypatch.setattr("server.reconstruction_interruption_triage.prioritized_review_queue",
                        lambda events, limit: _queue(_candidate()))
    rows = [_event("a", "2026-01-01T00:00:00Z"),
            _event("a", "2026-01-01T01:00:00Z"),
            _event("b", "2026-01-01T00:10:00Z")]
    assert interruption_review(rows, consent=True)["proposals"] == []


def test_no_consent_and_invalid_input():
    with pytest.raises(PermissionError):
        interruption_review([])
    with pytest.raises(ValueError):
        interruption_review([], consent=True, pause_seconds=0)


def test_actual_production_queue_no_time_only_candidates():
    rows = [_event("a", "2026-01-01T00:00:00Z"),
            _event("b", "2026-01-01T00:01:00Z")]
    result = interruption_review(rows, consent=True)
    assert result["proposals"] == []
    assert result["canonical_membership_unchanged"] is True
