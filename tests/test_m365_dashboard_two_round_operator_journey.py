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


@pytest.mark.parametrize("bad_result", [
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 1,
     "checkpoint_predecessor_generation": 1, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 2,
     "checkpoint_predecessor_generation": 0, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": True,
     "checkpoint_predecessor_generation": 0, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 1,
     "checkpoint_predecessor_generation": 0, "reviewed_event_snapshots": -1,
     "removal_markers_for_review": 0},
    {"status": "review_ready_for_explicit_commit", "proposed_generation": 1,
     "checkpoint_predecessor_generation": 0, "reviewed_event_snapshots": 2,
     "removal_markers_for_review": True},
    None,
])
def test_transaction_preflight_rejects_invalid_transition_without_commit(monkeypatch, bad_result):
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_reviewed_transaction.preflight", return_value=bad_result),
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        with pytest.raises(HTTPException) as error:
            operator_transaction_preflight(request(), True, FIRST)
        assert error.value.status_code == 409
        assert "secret-" not in str(error.value.detail)
        commit.assert_not_called()


@pytest.mark.parametrize("receipt", [
    None,
    {},
    {"status": "checkpoint_committed", "generation": 0},
    {"status": "checkpoint_committed", "generation": True},
    {"status": "checkpoint_committed", "generation": "1"},
    {"status": "checkpoint_committed", "generation": 1, "idempotent_replay": 1},
    {"status": "append_succeeded", "generation": 1},
])
def test_reviewed_commit_rejects_untrusted_success_receipt_without_retry(monkeypatch, receipt):
    """A malformed result after append is uncertain, never a confirmed checkpoint."""
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_reviewed_transaction.execute", return_value=receipt) as execute,
    ):
        with pytest.raises(HTTPException) as error:
            operator_reviewed_commit(request(), FIRST, **COMMIT_APPROVALS)
        assert error.value.status_code == 409
        assert "Do not retry" in str(error.value.detail)
        assert "secret-" not in str(error.value.detail)
        execute.assert_called_once()


@pytest.mark.parametrize("route_name,adapter_name,extra,adapter_result", [
    ("operator_reviewed_commit", "server.m365_outlook_reviewed_transaction.execute",
     {"reviewed_csv_sha256": FIRST, **COMMIT_APPROVALS},
     {"status": "checkpoint_committed", "generation": 1}),
    ("operator_next_round_prepare", "server.m365_next_round_review.prepare",
     {"expected_generation": 1, "expected_previous_candidate_sha256": FIRST,
      **EXTRACT_APPROVALS},
     {"status": "operator_review_required", "reviewed_csv_sha256": SECOND,
      "proposed_generation": 2, "event_snapshots": 1,
      "removal_markers_for_review": 0}),
    ("operator_recovery_triage", "server.m365_outlook_recovery_triage.triage",
     {"consent_local_read": True, "confirm_removals_reviewed": True},
     {"status": "manual_reconciliation_required"}),
])
def test_dashboard_routes_use_configured_local_owg_port(
        monkeypatch, route_name, adapter_name, extra, adapter_result):
    """The local authenticated OWG adapter must not silently use port 8765."""
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    monkeypatch.setenv("OWG_M365_OWG_PORT", "9876")
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch(adapter_name, return_value=adapter_result) as adapter,
    ):
        body(getattr(routes, route_name)(request(), **extra))
        assert adapter.call_count == 1
        assert adapter.call_args.kwargs["port"] == 9876


@pytest.mark.parametrize("invalid_port", ["0", "65536", "-1", "1.5", "http://host", ""])
@pytest.mark.parametrize("route_name,adapter_name,extra", [
    ("operator_reviewed_commit", "server.m365_outlook_reviewed_transaction.execute",
     {"reviewed_csv_sha256": FIRST, **COMMIT_APPROVALS}),
    ("operator_next_round_prepare", "server.m365_next_round_review.prepare",
     {"expected_generation": 1, "expected_previous_candidate_sha256": FIRST,
      **EXTRACT_APPROVALS}),
    ("operator_recovery_triage", "server.m365_outlook_recovery_triage.triage",
     {"consent_local_read": True, "confirm_removals_reviewed": True}),
])
def test_invalid_configured_owg_port_fails_closed_without_adapter_io(
        monkeypatch, invalid_port, route_name, adapter_name, extra):
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    monkeypatch.setenv("OWG_M365_OWG_PORT", invalid_port)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
        patch(adapter_name) as adapter,
    ):
        with pytest.raises(HTTPException) as error:
            getattr(routes, route_name)(request(), **extra)
        assert error.value.status_code == 409
        adapter.assert_not_called()


@pytest.mark.parametrize("status,next_action", [
    ("receipt_verified", "operator_may_separately_prepare_handoff"),
    ("handoff_verified", "operator_may_separately_authorize_next_extraction"),
    ("manual_reconciliation_required", "inspect_local_evidence_and_authenticated_owg_history"),
])
def test_recovery_triage_exposes_only_reviewed_manual_next_action(
        monkeypatch, status, next_action):
    """Authenticated readback is advisory, never a second append or checkpoint."""
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    with (
        patch.object(routes, "dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_recovery_triage.triage",
              return_value={"status": status, "token": "secret-token"}) as triage,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        result = body(routes.operator_recovery_triage(
            request(), consent_local_read=True, confirm_removals_reviewed=True))
    assert result["next_action"] == next_action
    assert result["automatic_retry_allowed"] is False
    assert result["checkpoint_advanced"] is False
    assert result["owg_writes"] == 0
    assert result["provider_requests"] == 0
    triage.assert_called_once()
    commit.assert_not_called()


@pytest.mark.parametrize("receipt", [
    None, [], "receipt_verified", {}, {"status": "unknown"},
    {"status": None}, {"status": 1},
])
def test_recovery_triage_refuses_malformed_receipt_without_retry(monkeypatch, receipt):
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    with (
        patch.object(routes, "dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_recovery_triage.triage", return_value=receipt) as triage,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        with pytest.raises(HTTPException) as error:
            routes.operator_recovery_triage(
                request(), consent_local_read=True, confirm_removals_reviewed=True)
    assert error.value.status_code == 409
    assert "Do not retry" in str(error.value.detail)
    triage.assert_called_once()
    commit.assert_not_called()


@pytest.mark.parametrize("consent_local_read,confirm_removals_reviewed", [
    (False, False), (False, True), (True, False),
])
def test_recovery_triage_requires_independent_approvals_before_io(
        monkeypatch, consent_local_read, confirm_removals_reviewed):
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    with (
        patch.object(routes, "dashboard_session_valid", return_value=True),
        patch("server.m365_outlook_recovery_triage.triage") as triage,
        pytest.raises(HTTPException) as error,
    ):
        routes.operator_recovery_triage(
            request(), consent_local_read=consent_local_read,
            confirm_removals_reviewed=confirm_removals_reviewed)
    assert error.value.status_code == 403
    triage.assert_not_called()


@pytest.mark.parametrize("route_name,adapter_name,extra", [
    ("operator_identity_folder_preflight", "server.m365_outlook_mailbox_identity.preflight",
     {"consent_graph_read": True, "consent_private_report": True}),
    ("operator_candidate_review", "server.m365_outlook_candidate_review.review",
     {"consent_local_read": True}),
    ("operator_transaction_preflight", "server.m365_outlook_reviewed_transaction.preflight",
     {"consent_local_read": True, "reviewed_csv_sha256": FIRST}),
    ("operator_reviewed_commit", "server.m365_outlook_reviewed_transaction.execute",
     {"reviewed_csv_sha256": FIRST, **COMMIT_APPROVALS}),
    ("operator_next_round_prepare", "server.m365_next_round_review.prepare",
     {"expected_generation": 1, "expected_previous_candidate_sha256": FIRST,
      **EXTRACT_APPROVALS}),
    ("operator_recovery_triage", "server.m365_outlook_recovery_triage.triage",
     {"consent_local_read": True, "confirm_removals_reviewed": True}),
])
@pytest.mark.parametrize("authorization", [
    None, "Bearer valid", "OWG-Session invalid", "OWG-Session ",
])
def test_all_operator_boundaries_refuse_unauthenticated_provider_and_local_io(
        monkeypatch, route_name, adapter_name, extra, authorization):
    """Consent alone must never authorize Graph reads, OWG writes or recovery."""
    from server import m365_operator_audit_routes as routes
    configure(monkeypatch)
    headers = [] if authorization is None else [
        (b"authorization", authorization.encode("ascii"))]
    untrusted = Request({"type": "http", "method": "POST", "headers": headers})
    with (
        patch.object(routes, "dashboard_session_valid",
              return_value=False) as validate,
        patch(adapter_name) as adapter,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        with pytest.raises(HTTPException) as error:
            getattr(routes, route_name)(untrusted, **extra)
    assert error.value.status_code == 403
    adapter.assert_not_called()
    commit.assert_not_called()
    if authorization is None or not authorization.lower().startswith("owg-session "):
        validate.assert_not_called()


def test_actual_http_operator_routes_preserve_review_commit_boundaries(monkeypatch):
    """Exercise FastAPI request parsing, dashboard auth and real route wiring.

    Adapter responses are provider-shaped fixtures, not real-tenant validation.
    The HTTP boundary must not turn a read-only review into a write.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server.m365_operator_audit_routes import router

    configure(monkeypatch)
    app = FastAPI()
    app.include_router(router)
    events = []
    def adapter(name, result):
        def invoke(**kwargs):
            events.append(name)
            assert kwargs["tenant"] == "secret-tenant"
            assert kwargs["mailbox"] == "secret-mailbox"
            return result
        return invoke

    headers = {"Authorization": "OWG-Session valid"}
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid",
              side_effect=lambda session: session == "valid"),
        patch("server.m365_outlook_mailbox_identity.preflight",
              side_effect=adapter("identity", {
                  "status": "mailbox_identity_observed",
                  "mailbox_identifier_matched": True, "tenant_id_verified": False})),
        patch("server.m365_outlook_folder_preflight.preflight",
              side_effect=adapter("folder", {
                  "status": "folder_metadata_observed", "folder_id_matched": True})),
        patch("server.m365_outlook_candidate_review.review",
              side_effect=adapter("review", {
                  "status": "review_ready", "csv_sha256": FIRST,
                  "event_snapshots": 2, "removal_markers_for_review": 1,
                  "pages": 1})),
        patch("server.m365_outlook_reviewed_transaction.preflight",
              side_effect=adapter("preflight", {
                  "status": "review_ready_for_explicit_commit",
                  "proposed_generation": 1,
                  "checkpoint_predecessor_generation": 0,
                  "reviewed_event_snapshots": 2,
                  "removal_markers_for_review": 1})),
        patch("server.m365_outlook_reviewed_transaction.execute",
              side_effect=adapter("commit", {
                  "status": "checkpoint_committed", "generation": 1})),
        patch("server.m365_next_round_review.prepare",
              side_effect=adapter("next", {
                  "status": "operator_review_required",
                  "reviewed_csv_sha256": SECOND, "proposed_generation": 2,
                  "event_snapshots": 1, "removal_markers_for_review": 0})),
        TestClient(app) as client,
    ):
        def post(path, params):
            response = client.post("/v1/m365/" + path, params=params,
                                   headers=headers)
            assert response.headers.get("cache-control") == "no-store" or response.status_code != 200
            assert "secret-token" not in response.text
            assert "/private/" not in response.text
            return response

        assert post("operator-identity-folder-preflight", {
            "consent_graph_read": True,
            "consent_private_report": True}).json()["sync_authorized"] is False
        assert post("operator-candidate-review", {
            "consent_local_read": True}).json()["csv_sha256"] == FIRST
        assert post("operator-transaction-preflight", {
            "consent_local_read": True,
            "reviewed_csv_sha256": FIRST}).json()["checkpoint_advanced"] is False
        assert events == ["identity", "folder", "review", "preflight"]
        assert post("operator-reviewed-commit", {
            "reviewed_csv_sha256": FIRST, **{
                **COMMIT_APPROVALS, "confirm_removals_reviewed": False}
        }).status_code == 403
        assert "commit" not in events
        assert post("operator-reviewed-commit", {
            "reviewed_csv_sha256": FIRST, **COMMIT_APPROVALS
        }).json()["generation"] == 1
        assert post("operator-next-round-prepare", {
            "expected_generation": 1,
            "expected_previous_candidate_sha256": FIRST,
            **EXTRACT_APPROVALS
        }).json()["reviewed_csv_sha256"] == SECOND
        assert events == ["identity", "folder", "review", "preflight",
                          "commit", "next"]
        assert client.post("/v1/m365/operator-reviewed-commit",
                           params={"reviewed_csv_sha256": FIRST, **COMMIT_APPROVALS}
                           ).status_code == 403
        assert events.count("commit") == 1


def test_identity_preflight_session_revocation_blocks_second_graph_read(monkeypatch):
    """A session revoked during the first Graph request cannot authorize the second."""
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid",
              side_effect=[True, False]) as session_valid,
        patch("server.m365_outlook_mailbox_identity.preflight",
              return_value={"status": "mailbox_identity_observed",
                            "mailbox_identifier_matched": True,
                            "tenant_id_verified": False}) as identity,
        patch("server.m365_outlook_folder_preflight.preflight") as folder,
    ):
        with pytest.raises(HTTPException) as error:
            operator_identity_folder_preflight(request(), True, True)
    assert error.value.status_code == 409
    assert "Do not retry" in str(error.value.detail)
    assert session_valid.call_count == 2
    identity.assert_called_once()
    folder.assert_not_called()


def test_identity_preflight_session_revocation_during_folder_read_refuses_success(monkeypatch):
    """A second Graph read completing after session revocation cannot report success."""
    configure(monkeypatch)
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid",
              side_effect=[True, True, False]) as session_valid,
        patch("server.m365_outlook_mailbox_identity.preflight",
              return_value={"status": "mailbox_identity_observed",
                            "mailbox_identifier_matched": True,
                            "tenant_id_verified": False}) as identity,
        patch("server.m365_outlook_folder_preflight.preflight",
              return_value={"status": "folder_metadata_observed",
                            "folder_id_matched": True}) as folder,
    ):
        with pytest.raises(HTTPException) as error:
            operator_identity_folder_preflight(request(), True, True)
    assert error.value.status_code == 409
    assert "Do not retry" in str(error.value.detail)
    assert session_valid.call_count == 3
    identity.assert_called_once()
    folder.assert_called_once()


def test_candidate_digest_not_disclosed_if_session_revoked_during_review(monkeypatch):
    """A read-only review cannot publish a digest after the dashboard session expires."""
    configure(monkeypatch)
    reviewed = {"status": "review_ready", "event_snapshots": 2,
                "removal_markers_for_review": 0, "pages": 1,
                "csv_sha256": FIRST}
    with (
        patch("server.m365_operator_audit_routes.dashboard_session_valid",
              side_effect=[True, False]) as valid,
        patch("server.m365_outlook_candidate_review.review",
              return_value=reviewed) as review,
        patch("server.m365_outlook_reviewed_transaction.execute") as commit,
    ):
        with pytest.raises(HTTPException) as error:
            operator_candidate_review(request(), True)
    assert error.value.status_code == 409
    assert FIRST not in str(error.value.detail)
    assert valid.call_count == 2
    review.assert_called_once()
    commit.assert_not_called()
