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


@pytest.mark.parametrize("bad_result", [
    {"status": "operator_review_required", "reviewed_csv_sha256": SECOND,
     "proposed_generation": 1, "event_snapshots": 2, "removal_markers_for_review": 0},
    {"status": "operator_review_required", "reviewed_csv_sha256": "not-a-digest",
     "proposed_generation": 2, "event_snapshots": 2, "removal_markers_for_review": 0},
    {"status": "operator_review_required", "reviewed_csv_sha256": SECOND,
     "proposed_generation": 2, "event_snapshots": True, "removal_markers_for_review": 0},
    {"status": "operator_review_required", "reviewed_csv_sha256": SECOND,
     "proposed_generation": 2, "event_snapshots": 2, "removal_markers_for_review": -1},
    None,
])
def test_next_round_rejects_malformed_or_stale_adapter_output(monkeypatch, bad_result):
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch("server.m365_next_round_review.prepare", return_value=bad_result) as prepare,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        with pytest.raises(HTTPException) as error:
            operator_next_round_prepare(request(), 1, FIRST, **EXTRACT_APPROVALS)
        assert error.value.status_code == 409
        assert "Do not retry" in str(error.value.detail)
        prepare.assert_called_once()
        commit.assert_not_called()


@pytest.mark.parametrize("failure_phase", ["append", "handoff", "checkpoint"])
def test_real_transaction_orchestrator_never_retries_uncertain_write(
        monkeypatch, failure_phase):
    """Exercise production execute orchestration, not a mocked dashboard route.

    A failure at or after the OWG append boundary must not trigger a second
    append, a speculative checkpoint, or a silent successful receipt.
    """
    from server import m365_outlook_reviewed_transaction as transaction
    from server.m365_outlook_owg_receipt import UncertainReceipt

    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    events = []
    reviewed = {"csv_sha256": FIRST, "event_snapshots": 2,
                "removal_markers_for_review": 1}

    def phase(name, result=None, failure=False):
        def run(**kwargs):
            events.append(name)
            if failure:
                raise UncertainReceipt("manual reconciliation required")
            return result
        return run

    kwargs = dict(
        tenant="tenant", mailbox="mailbox", folder="folder", log_id="log",
        confirm_log_id="log", grant_file="/private/grant",
        candidate="/private/candidate", csv_path="/private/events.csv",
        receipt_file="/private/receipt", journal_dir="/private/journal",
        handoff_dir="/private/handoff", ledger_dir="/private/ledger",
        successor_label="next", expected_review_csv_sha256=FIRST,
        expected_previous_candidate_sha256=None,
        consent_local_read=True, consent_local_append=True,
        confirm_candidate_reviewed=True, confirm_removals_reviewed=True,
        confirm_checkpoint_commit=True,
    )
    with (
        patch.object(transaction.cycle, "review", side_effect=phase("review", reviewed)),
        patch.object(transaction, "inspect_checkpoint", return_value=None),
        patch.object(transaction.cycle, "append", side_effect=phase(
            "append", {"status": "receipt_persisted"},
            failure_phase == "append")),
        patch.object(transaction.cycle, "handoff", side_effect=phase(
            "handoff", {"status": "next_round_handoff_prepared"},
            failure_phase == "handoff")),
        patch.object(transaction.cycle, "checkpoint_commit", side_effect=phase(
            "checkpoint", {"generation": 1},
            failure_phase == "checkpoint")),
        pytest.raises(UncertainReceipt),
    ):
        transaction.execute(**kwargs)
    assert events.count("append") == 1
    assert events.count("handoff") == (0 if failure_phase == "append" else 1)
    assert events.count("checkpoint") == (1 if failure_phase == "checkpoint" else 0)


@pytest.mark.parametrize("approval", [
    "consent_local_read", "consent_local_append",
    "confirm_candidate_reviewed", "confirm_removals_reviewed",
    "confirm_checkpoint_commit",
])
def test_real_transaction_rejects_missing_independent_approval_before_any_io(
        monkeypatch, approval):
    from server import m365_outlook_reviewed_transaction as transaction
    from server.m365_outlook_owg_receipt import ReceiptError

    monkeypatch.delenv("OWG_M365_AUDIT_DIR", raising=False)
    approvals = dict(COMMIT_APPROVALS)
    approvals[approval] = False
    with (
        patch.object(transaction.cycle, "review") as review,
        patch.object(transaction.cycle, "append") as append,
        patch.object(transaction.cycle, "checkpoint_commit") as checkpoint,
        pytest.raises(ReceiptError),
    ):
        transaction.execute(
            tenant="tenant", mailbox="mailbox", folder="folder",
            log_id="log", confirm_log_id="log", grant_file="/private/grant",
            candidate="/private/candidate", csv_path="/private/events.csv",
            receipt_file="/private/receipt", journal_dir="/private/journal",
            handoff_dir="/private/handoff", ledger_dir="/private/ledger",
            successor_label="next", expected_review_csv_sha256=FIRST,
            **approvals,
        )
    review.assert_not_called()
    append.assert_not_called()
    checkpoint.assert_not_called()
