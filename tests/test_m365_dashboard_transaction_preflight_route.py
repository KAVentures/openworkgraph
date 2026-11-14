"""Dashboard transaction preflight is authenticated, scoped, consented and read-only."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.m365_operator_audit_routes import operator_transaction_preflight


DIGEST = "a" * 64


def _request():
    return Request({"type": "http", "method": "POST",
                    "path": "/v1/m365/operator-transaction-preflight",
                    "headers": [(b"authorization", b"OWG-Session valid")]})


def _configure(monkeypatch):
    for name, value in (
        ("OWG_M365_TENANT", "private-tenant"),
        ("OWG_M365_MAILBOX", "private-mailbox"),
        ("OWG_M365_FOLDER", "private-folder"),
        ("OWG_M365_LOG_ID", "private-log"),
        ("OWG_M365_GRANT_FILE", "/private/grant"),
        ("OWG_M365_CANDIDATE_FILE", "/private/candidate"),
        ("OWG_M365_LEDGER_DIR", "/private/ledger"),
        ("OWG_M365_PREVIOUS_CANDIDATE_SHA256", "b" * 64),
    ):
        monkeypatch.setenv(name, value)


def test_auth_consent_and_exact_digest_gate():
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as exc:
            operator_transaction_preflight(_request(), True, DIGEST)
    assert exc.value.status_code == 403
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        with pytest.raises(HTTPException) as exc:
            operator_transaction_preflight(_request(), False, DIGEST)
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            operator_transaction_preflight(_request(), True, "not-a-digest")
        assert exc.value.status_code == 422


def test_config_missing_does_not_call_preflight(monkeypatch):
    for name in ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
                 "OWG_M365_LOG_ID", "OWG_M365_GRANT_FILE",
                 "OWG_M365_CANDIDATE_FILE", "OWG_M365_LEDGER_DIR"):
        monkeypatch.delenv(name, raising=False)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_reviewed_transaction.preflight") as preflight):
        result = operator_transaction_preflight(_request(), True, DIGEST)
    assert json.loads(result.body)["configured"] is False
    preflight.assert_not_called()


def test_readonly_proposal_is_redacted_and_scoped(monkeypatch):
    _configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_reviewed_transaction.preflight", return_value={
              "status": "review_ready_for_explicit_commit",
              "proposed_generation": 2, "checkpoint_predecessor_generation": 1,
              "reviewed_event_snapshots": 4, "removal_markers_for_review": 1,
              "scope_sha256": "secret-scope",
          }) as preflight):
        response = operator_transaction_preflight(_request(), True, DIGEST)
    body = json.loads(response.body)
    assert body["review_ready"] is True
    assert body["manual_review_and_separate_commit_required"] is True
    assert body["provider_requests"] == body["owg_writes"] == 0
    assert body["checkpoint_advanced"] is False
    assert body["automatic_retry_allowed"] is False
    assert response.headers["cache-control"] == "no-store"
    assert all(secret not in response.body.decode() for secret in
               ("private-tenant", "private-mailbox", "private-log", "secret-scope", "/private"))
    assert preflight.call_args.kwargs["expected_review_csv_sha256"] == DIGEST
    assert preflight.call_args.kwargs["expected_previous_candidate_sha256"] == "b" * 64
    assert preflight.call_args.kwargs["candidate"] == Path("/private/candidate")


def test_stale_checkpoint_and_revocation_fail_closed(monkeypatch):
    _configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_reviewed_transaction.preflight",
                side_effect=ValueError("secret stale checkpoint"))):
        with pytest.raises(HTTPException) as exc:
            operator_transaction_preflight(_request(), True, DIGEST)
    assert exc.value.status_code == 409
    assert "secret" not in str(exc.value.detail)


def test_dashboard_requires_fresh_preflight_consent():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Check transaction preflight" in page
    assert "preflightConsent.checked" in page
    assert "preflightConsent.checked = false" in page
    assert "operator-transaction-preflight?consent_local_read=true" in page
    assert "Separate approvals required." in page
