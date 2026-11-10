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
