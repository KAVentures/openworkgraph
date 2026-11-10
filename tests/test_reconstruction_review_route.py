"""Grant/revocation regression checks for local reconstruction review API."""
import json
import sqlite3

import pytest
from fastapi import HTTPException

from server import history_routes
from server import work_resumption
from shared import history_policy


def test_review_denies_without_explicit_saved_history_grant(monkeypatch):
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: {"mode": "off"})
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_review_rechecks_revocation_before_returning_evidence(monkeypatch):
    calls = iter(({"mode": "all_saved"}, {"mode": "off"}))
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: next(calls))
    monkeypatch.setattr(history_policy, "history_generation", lambda: 1)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda *_args, **_kwargs: {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "observed_pair_event_assignments": [], "coverage": {},
    })
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_review_empty_granted_response_remains_non_authoritative(monkeypatch):
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: {"mode": "all_saved"})
    monkeypatch.setattr(history_policy, "history_generation", lambda: 1)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda *_args, **_kwargs: {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "observed_pair_event_assignments": [], "coverage": {"scan_truncated": False},
    })
    response = history_routes.get_reconstruction_review()
    body = json.loads(response.body)
    assert response.headers["cache-control"] == "no-store"
    assert body["authoritative"] is False
    assert body["review"]["event_reviews"] == []
    assert body["review"]["no_case_identity_inferred"] is True


def test_review_denies_grant_replacement_even_if_still_all_saved(monkeypatch):
    # Regranting while a query is running must invalidate the original lease.
    before = {"mode": "all_saved", "granted_at": "2026-11-10T01:00:00+00:00"}
    after = {"mode": "all_saved", "granted_at": "2026-11-10T01:01:00+00:00"}
    calls = iter((before, after))
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: next(calls))
    monkeypatch.setattr(history_policy, "history_generation", lambda: 1)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda *_args, **_kwargs: {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "observed_pair_event_assignments": [], "coverage": {},
    })
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_review_denies_revocation_during_queue_construction(monkeypatch):
    """A grant revoked while the review queue is assembled must not disclose it."""
    from server import reconstruction_review_cli

    access = {"mode": "all_saved", "granted_at": "2026-11-10T01:00:00+00:00"}
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: dict(access))
    monkeypatch.setattr(history_policy, "history_generation", lambda: 1)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda *_args, **_kwargs: {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "observed_pair_event_assignments": [], "coverage": {},
    })

    def revoke_while_building(_result):
        access["mode"] = "off"
        return {"event_reviews": [], "no_case_identity_inferred": True}

    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", revoke_while_building)
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403
