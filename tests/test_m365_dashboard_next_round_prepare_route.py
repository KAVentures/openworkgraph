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
