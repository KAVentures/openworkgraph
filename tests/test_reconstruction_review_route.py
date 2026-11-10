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


def test_review_route_preserves_competing_event_provenance_without_confirming_case(monkeypatch):
    """API contract: one event co-observed with two pairs stays ambiguous."""
    monkeypatch.setattr(history_routes, "active_ai_history_access",
                        lambda: {"mode": "all_saved", "granted_at": "fixed"})
    monkeypatch.setattr(history_policy, "history_generation", lambda: 7)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))

    def observed_pair(issue, file_ref):
        return {
            "resource_pointers": [
                {"provider": "github", "resource_kind": "issue", "resource_ref": issue},
                {"provider": "local_file", "resource_kind": "file", "resource_ref": file_ref},
            ],
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
            "current_evidence_event_ids": ["shared-event"],
            "earlier_corroborations": [],
        }

    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda *_args, **_kwargs: {
                            "derived": True, "authoritative": False,
                            "saved_history_access": "all_saved",
                            "observed_pair_event_assignments": [
                                observed_pair("case-A", "file-A"),
                                observed_pair("case-B", "file-B"),
                            ],
                            "coverage": {"scan_truncated": False},
                        })
    response = history_routes.get_reconstruction_review()
    body = json.loads(response.body)
    rows = body["review"]["event_reviews"]
    assert len(rows) == 1
    assert rows[0]["event_id"] == "shared-event"
    assert rows[0]["review_status"] == "ambiguous_multiple_pairs"
    assert rows[0]["confirmed_case"] is False
    assert len(rows[0]["candidate_resource_pairs"]) == 2
    assert {tuple(pointer) for pair in rows[0]["candidate_resource_pairs"]
            for pointer in pair if pointer[0] == "github"} == {
                ("github", "issue", "case-A"), ("github", "issue", "case-B")}
    assert body["authoritative"] is False
    assert response.headers["cache-control"] == "no-store"


def test_review_isolates_interleaved_cases_with_reused_event_ids(monkeypatch):
    """An earlier session's event ID must not contaminate a current case."""
    monkeypatch.setattr(history_routes, "active_ai_history_access",
                        lambda: {"mode": "all_saved", "granted_at": "fixed"})
    monkeypatch.setattr(history_policy, "history_generation", lambda: 7)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))

    def pair(issue, current, earlier):
        return {
            "resource_pointers": [
                {"provider": "github", "resource_kind": "issue", "resource_ref": issue},
                {"provider": "local_file", "resource_kind": "file", "resource_ref": "file-" + issue},
            ],
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
            "current_evidence_event_ids": current,
            "earlier_corroborations": [{
                "session_started_at": "2026-11-09T10:00:00Z",
                "evidence_event_ids": earlier,
            }] if earlier else [],
        }

    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda *_args, **_kwargs: {
                            "derived": True, "authoritative": False,
                            "saved_history_access": "all_saved",
                            "observed_pair_event_assignments": [
                                pair("A", ["reused", "interleave"], []),
                                pair("B", ["interleave"], ["reused"]),
                            ],
                            "coverage": {"scan_truncated": False},
                        })
    body = json.loads(history_routes.get_reconstruction_review().body)
    rows = body["review"]["event_reviews"]
    assert len(rows) == 3
    by_window = {(row["window"], row["event_id"]): row for row in rows}
    earlier = by_window[("earlier:2026-11-09T10:00:00Z", "reused")]
    current = by_window[("current", "reused")]
    interleaved = by_window[("current", "interleave")]
    assert earlier["candidate_resource_pairs"] != current["candidate_resource_pairs"]
    assert len(earlier["candidate_resource_pairs"]) == 1
    assert len(current["candidate_resource_pairs"]) == 1
    assert len(interleaved["candidate_resource_pairs"]) == 2
    assert interleaved["review_status"] == "ambiguous_multiple_pairs"
    assert all(row["confirmed_case"] is False for row in rows)


def test_review_denies_retention_generation_change_after_query(monkeypatch):
    monkeypatch.setattr(history_routes, "active_ai_history_access",
                        lambda: {"mode": "all_saved", "granted_at": "fixed"})
    generations = iter((7, 8))
    monkeypatch.setattr(history_policy, "history_generation", lambda: next(generations))
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda *_args, **_kwargs: {
                            "derived": True, "authoritative": False,
                            "saved_history_access": "all_saved",
                            "observed_pair_event_assignments": [], "coverage": {},
                        })
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_review_denies_revocation_during_json_serialization(monkeypatch):
    """Consent revoked while encoding the response must deny disclosure."""
    access = {"mode": "all_saved", "granted_at": "fixed"}
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: dict(access))
    monkeypatch.setattr(history_policy, "history_generation", lambda: 7)
    monkeypatch.setattr(history_routes, "connect", lambda: sqlite3.connect(":memory:"))
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda *_args, **_kwargs: {
        "derived": True, "authoritative": False, "saved_history_access": "all_saved",
        "observed_pair_event_assignments": [], "coverage": {},
    })
    original_dumps = json.dumps

    def revoke_during_encoding(*args, **kwargs):
        encoded = original_dumps(*args, **kwargs)
        access["mode"] = "off"
        return encoded

    monkeypatch.setattr(history_routes.json, "dumps", revoke_during_encoding)
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403
