"""Authenticated bounded Microsoft 365 next-round dashboard contract."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from server.m365_operator_audit_routes import operator_next_round_prepare

DIGEST = "a" * 64
APPROVALS = dict(consent_local_read=True, consent_delta_read=True,
                 consent_local_export=True, confirm_previous_removals_reviewed=True)
CONFIG = {
    "OWG_M365_TENANT": "secret-tenant",
    "OWG_M365_MAILBOX": "secret-mailbox",
    "OWG_M365_FOLDER": "secret-folder",
    "OWG_M365_LOG_ID": "secret-log",
    "OWG_M365_GRANT_FILE": "/private/grant",
    "OWG_M365_PREVIOUS_CANDIDATE_FILE": "/private/previous.json",
    "OWG_M365_PREVIOUS_CSV_PATH": "/private/previous.csv",
    "OWG_M365_RECEIPT_FILE": "/private/receipt",
    "OWG_M365_JOURNAL_DIR": "/private/journal",
    "OWG_M365_HANDOFF_DIR": "/private/handoff",
    "OWG_M365_LEDGER_DIR": "/private/ledger",
    "OWG_M365_SUCCESSOR_LABEL": "successor",
    "OWG_M365_CSV_PATH": "/private/next.csv",
    "OWG_M365_CANDIDATE_FILE": "/private/next.json",
    "OWG_M365_GRAPH_TOKEN": "secret-token",
}


def request():
    return Request({"type": "http", "method": "POST",
                    "path": "/v1/m365/operator-next-round-prepare",
                    "headers": [(b"authorization", b"OWG-Session valid")]})


def configure(monkeypatch):
    for key, value in CONFIG.items():
        monkeypatch.setenv(key, value)


def test_auth_and_four_separate_consents():
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as exc:
            operator_next_round_prepare(request(), 1, DIGEST, **APPROVALS)
        assert exc.value.status_code == 403
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_next_round_review.prepare") as prepare):
        for key in APPROVALS:
            missing = dict(APPROVALS)
            missing[key] = False
            with pytest.raises(HTTPException) as exc:
                operator_next_round_prepare(request(), 1, DIGEST, **missing)
            assert exc.value.status_code == 403
        for generation, digest in [(0, DIGEST), (1, "invalid"), (-1, DIGEST)]:
            with pytest.raises(HTTPException) as exc:
                operator_next_round_prepare(request(), generation, digest, **APPROVALS)
            assert exc.value.status_code == 422
        prepare.assert_not_called()


def test_missing_credentials_fail_closed(monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("OWG_M365_GRAPH_TOKEN")
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_next_round_review.prepare") as prepare):
        with pytest.raises(HTTPException) as exc:
            operator_next_round_prepare(request(), 1, DIGEST, **APPROVALS)
    assert exc.value.status_code == 409
    prepare.assert_not_called()


def test_bounded_delegate_and_redacted_review(monkeypatch):
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_next_round_review.prepare", return_value={
              "status": "operator_review_required", "reviewed_csv_sha256": "b" * 64,
              "proposed_generation": 2, "event_snapshots": 4,
              "removal_markers_for_review": 1, "cursor_url": "secret-cursor",
          }) as prepare):
        response = operator_next_round_prepare(request(), 1, DIGEST, **APPROVALS)
    result = json.loads(response.body)
    assert result["status"] == "operator_review_required"
    assert result["checkpoint_advanced"] is False
    assert result["owg_append_performed"] is False
    assert result["automatic_retry_allowed"] is False
    assert response.headers["cache-control"] == "no-store"
    assert all(value not in response.body.decode() for value in
               ("secret-token", "secret-tenant", "secret-cursor", "/private"))
    kwargs = prepare.call_args.kwargs
    assert kwargs["expected_generation"] == 1
    assert kwargs["expected_previous_candidate_sha256"] == DIGEST
    assert kwargs["token"] == "secret-token"
    assert kwargs["previous_candidate"] == Path("/private/previous.json")
    assert kwargs["session"] == "valid"
    assert kwargs["require_event_membership"] is True


def test_provider_revocation_and_uncertain_extraction_no_retry(monkeypatch):
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_next_round_review.prepare",
                side_effect=ValueError("secret-token expired grant")) as prepare):
        with pytest.raises(HTTPException) as exc:
            operator_next_round_prepare(request(), 1, DIGEST, **APPROVALS)
    assert exc.value.status_code == 409
    assert "secret-token" not in str(exc.value.detail)
    assert "Do not retry" in str(exc.value.detail)
    prepare.assert_called_once()


def test_dashboard_requires_fresh_graph_approvals():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Prepare next Microsoft 365 Graph review" in page
    assert "operator-next-round-prepare?" in page
    assert "extractApprovals.every(({input}) => input.checked)" in page
    assert "extractApprovals.forEach(({input}) => { input.checked = false; })" in page
    assert "No automatic retry." in page


def test_recovery_triage_auth_consent_and_minimized_decision(monkeypatch):
    from server.m365_operator_audit_routes import operator_recovery_triage
    configure(monkeypatch)
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as exc:
            operator_recovery_triage(request(), True, True)
        assert exc.value.status_code == 403
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_recovery_triage.triage") as triage):
        for approvals in [(False, True), (True, False)]:
            with pytest.raises(HTTPException) as exc:
                operator_recovery_triage(request(), *approvals)
            assert exc.value.status_code == 403
        triage.assert_not_called()
        triage.return_value = {
            "status": "manual_reconciliation_required",
            "next_action": "inspect_authenticated_owg_append_history_and_provider_grant",
            "secret": "secret-token",
        }
        response = operator_recovery_triage(request(), True, True)
        result = json.loads(response.body)
        assert result["status"] == "manual_reconciliation_required"
        assert result["automatic_retry_allowed"] is False
        assert result["checkpoint_advanced"] is False
        assert result["owg_writes"] == 0
        assert response.headers["cache-control"] == "no-store"
        assert "secret-token" not in response.body.decode()
        assert triage.call_args.kwargs["session"] == "valid"
        assert triage.call_args.kwargs["consent_local_read"] is True
        assert triage.call_args.kwargs["confirm_removals_reviewed"] is True


def test_recovery_triage_missing_config_and_fail_closed(monkeypatch):
    from server.m365_operator_audit_routes import operator_recovery_triage
    configure(monkeypatch)
    monkeypatch.delenv("OWG_M365_RECEIPT_FILE")
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_recovery_triage.triage") as triage):
        with pytest.raises(HTTPException) as exc:
            operator_recovery_triage(request(), True, True)
        assert exc.value.status_code == 409
        triage.assert_not_called()
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_recovery_triage.triage", side_effect=ValueError("secret-token"))):
        with pytest.raises(HTTPException) as exc:
            operator_recovery_triage(request(), True, True)
        assert exc.value.status_code == 409
        assert "secret-token" not in str(exc.value.detail)
        assert "Do not retry" in str(exc.value.detail)


def test_recovery_dashboard_requires_fresh_independent_approvals():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Inspect uncertain Microsoft 365 append" in page
    assert "operator-recovery-triage?consent_local_read=true&confirm_removals_reviewed=true" in page
    assert "if (!recoveryRead.checked || !recoveryRemoval.checked)" in page
    assert "recoveryRead.checked = false;" in page
    assert "recoveryRemoval.checked = false;" in page
    assert "No automatic retry." in page
