"""Operator journey regression: explicit review and checkpoint boundaries across two rounds.

This is an adapter orchestration contract with provider-shaped adapter outputs,
not a live Microsoft tenant test. The production adapters are exercised in
their separate provider contract harness.
"""
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.m365_operator_audit_routes import (
    operator_identity_folder_preflight, operator_candidate_review,
    operator_transaction_preflight, operator_reviewed_commit,
    operator_next_round_prepare, operator_recovery_triage,
)

FIRST = "a" * 64
SECOND = "b" * 64
COMMIT_APPROVALS = dict(
    consent_local_read=True, consent_local_append=True,
    confirm_candidate_reviewed=True, confirm_removals_reviewed=True,
    confirm_checkpoint_commit=True,
)
EXTRACT_APPROVALS = dict(
    consent_local_read=True, consent_delta_read=True,
    consent_local_export=True, confirm_previous_removals_reviewed=True,
)


def request():
    return Request({"type": "http", "method": "POST",
                    "headers": [(b"authorization", b"OWG-Session valid")]})


def configure(monkeypatch):
    values = {
        "TENANT": "secret-tenant", "MAILBOX": "secret-mailbox",
        "FOLDER": "secret-folder", "EXPECTED_ADDRESS": "secret@example.org",
        "GRANT_FILE": "/private/grant", "IDENTITY_REPORT": "/private/identity",
        "FOLDER_REPORT": "/private/folder-report", "GRAPH_TOKEN": "secret-token",
        "LOG_ID": "secret-log", "CANDIDATE_FILE": "/private/candidate",
        "LEDGER_DIR": "/private/ledger", "CSV_PATH": "/private/current.csv",
        "RECEIPT_FILE": "/private/receipt", "JOURNAL_DIR": "/private/journal",
        "HANDOFF_DIR": "/private/handoff", "SUCCESSOR_LABEL": "next",
        "PREVIOUS_CANDIDATE_FILE": "/private/previous.json",
        "PREVIOUS_CSV_PATH": "/private/previous.csv",
    }
    for key, value in values.items():
        monkeypatch.setenv("OWG_M365_" + key, value)


def body(response):
    assert response.headers["cache-control"] == "no-store"
    text = response.body.decode()
    for secret in ("secret-token", "secret-tenant", "secret-mailbox",
                   "secret-log", "secret@example.org", "/private/"):
        assert secret not in text
    return json.loads(text)


def test_two_round_operator_journey_requires_review_and_explicit_commit(monkeypatch):
    configure(monkeypatch)
    events = []

    def track(name, result):
        def invoke(**kwargs):
            events.append(name)
            assert kwargs["tenant"] == "secret-tenant"
            assert kwargs["mailbox"] == "secret-mailbox"
            assert kwargs["folder"] == "secret-folder"
            return result
        return invoke

    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_mailbox_identity.preflight", side_effect=track(
            "identity", {"status": "mailbox_identity_observed",
                         "mailbox_identifier_matched": True, "tenant_id_verified": False})),
        patch("server.m365_outlook_folder_preflight.preflight", side_effect=track(
            "folder", {"status": "folder_metadata_observed", "folder_id_matched": True})),
        patch("server.m365_outlook_candidate_review.review", side_effect=track(
            "review", {"status": "review_ready", "event_snapshots": 3,
                       "removal_markers_for_review": 1, "pages": 2, "csv_sha256": FIRST})),
        patch("server.m365_outlook_reviewed_transaction.preflight", side_effect=track(
            "transaction-preflight", {"status": "review_ready_for_explicit_commit",
                                      "proposed_generation": 1,
                                      "checkpoint_predecessor_generation": 0,
                                      "reviewed_event_snapshots": 3,
                                      "removal_markers_for_review": 1})),
        patch("server.m365_outlook_reviewed_transaction.execute", side_effect=track(
            "commit", {"status": "checkpoint_committed", "generation": 1})),
        patch("server.m365_next_round_review.prepare", side_effect=track(
            "next-round", {"status": "operator_review_required",
                           "reviewed_csv_sha256": SECOND, "proposed_generation": 2,
                           "event_snapshots": 2, "removal_markers_for_review": 0})),
    ):
        assert body(operator_identity_folder_preflight(request(), True, True))["sync_authorized"] is False
        assert body(operator_candidate_review(request(), True))["csv_sha256"] == FIRST
        assert body(operator_transaction_preflight(request(), True, FIRST))["checkpoint_advanced"] is False
        assert events == ["identity", "folder", "review", "transaction-preflight"]

        with pytest.raises(HTTPException) as error:
            operator_reviewed_commit(request(), FIRST, **{**COMMIT_APPROVALS,
                                                          "confirm_removals_reviewed": False})
        assert error.value.status_code == 403
        assert "commit" not in events
        assert body(operator_reviewed_commit(request(), FIRST, **COMMIT_APPROVALS))["generation"] == 1
        assert body(operator_next_round_prepare(request(), 1, FIRST,
                                                **EXTRACT_APPROVALS))["reviewed_csv_sha256"] == SECOND
        assert events == ["identity", "folder", "review", "transaction-preflight",
                          "commit", "next-round"]


def test_revoked_next_round_stops_without_commit_or_automatic_retry(monkeypatch):
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch("server.m365_next_round_review.prepare",
              side_effect=PermissionError("revoked secret-token")) as prepare,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        # Revocation must be a recoverable refusal, never an implicit commit.
        with pytest.raises(HTTPException) as error:
            operator_next_round_prepare(request(), 1, FIRST, **EXTRACT_APPROVALS)
        assert error.value.status_code == 409
        assert "secret-token" not in str(error.value.detail)
        assert "Do not retry" in str(error.value.detail)
        prepare.assert_called_once()
        commit.assert_not_called()
