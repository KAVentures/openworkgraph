"""Dashboard M365 candidate review is consented, scoped and read-only."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.m365_operator_audit_routes import operator_candidate_review


def _request():
    return Request({"type": "http", "method": "POST",
                    "path": "/v1/m365/operator-candidate-review",
                    "headers": [(b"authorization", b"OWG-Session valid")]})


def test_denies_missing_session_and_consent():
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as error:
            operator_candidate_review(_request(), consent_local_read=True)
    assert error.value.status_code == 403
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        with pytest.raises(HTTPException) as error:
            operator_candidate_review(_request(), consent_local_read=False)
    assert error.value.status_code == 403


def _configure(monkeypatch):
    for name, value in (
        ("OWG_M365_TENANT", "private-tenant"),
        ("OWG_M365_MAILBOX", "private-mailbox"),
        ("OWG_M365_FOLDER", "private-folder"),
        ("OWG_M365_GRANT_FILE", "/private/grant"),
        ("OWG_M365_CANDIDATE_FILE", "/private/candidate"),
    ):
        monkeypatch.setenv(name, value)


def test_unconfigured_never_reads(monkeypatch):
    for name in ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
                 "OWG_M365_GRANT_FILE", "OWG_M365_CANDIDATE_FILE"):
        monkeypatch.delenv(name, raising=False)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_candidate_review.review") as review):
        response = operator_candidate_review(_request(), consent_local_read=True)
    assert json.loads(response.body)["configured"] is False
    review.assert_not_called()


def test_review_only_and_redacted(monkeypatch):
    _configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_candidate_review.review", return_value={
              "status": "review_ready", "event_snapshots": 3,
              "removal_markers_for_review": 1, "pages": 2,
              "csv_sha256": "a" * 64, "scope_sha256": "secret-scope",
          }) as review):
        response = operator_candidate_review(_request(), consent_local_read=True)
    body = json.loads(response.body)
    assert body["review_ready"] is True
    assert body["separate_append_approval_required"] is True
    assert body["owg_writes"] == body["provider_requests"] == 0
    assert body["checkpoint_advanced"] is False
    assert body["automatic_retry_allowed"] is False
    assert response.headers["cache-control"] == "no-store"
    assert all(secret not in response.body.decode() for secret in
               ("private-tenant", "private-mailbox", "secret-scope", "/private"))
    assert review.call_args.kwargs["candidate"] == Path("/private/candidate")
    assert review.call_args.kwargs["consent_local_read"] is True


def test_expired_grant_is_not_empty_state(monkeypatch):
    _configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_candidate_review.review",
                side_effect=ValueError("secret grant expired"))):
        with pytest.raises(HTTPException) as error:
            operator_candidate_review(_request(), consent_local_read=True)
    assert error.value.status_code == 409
    assert "secret" not in str(error.value.detail)


def test_dashboard_requires_fresh_review_consent():
    page = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    assert "Review local candidate" in page
    assert "reviewConsent.checked" in page
    assert "reviewConsent.checked = false" in page
    assert "/v1/m365/operator-candidate-review?consent_local_read=true" in page
    assert "Separate approval required." in page
