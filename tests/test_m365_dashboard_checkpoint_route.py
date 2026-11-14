"""Local M365 checkpoint route: opt-in, scope isolation and secret redaction."""
import json
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.m365_operator_audit_routes import operator_checkpoint


def _request(authorization="OWG-Session valid"):
    return Request({"type": "http", "method": "POST",
                    "path": "/v1/m365/operator-checkpoint",
                    "headers": [(b"authorization", authorization.encode())]})


def test_denies_missing_session_and_separate_consent():
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as exc:
            operator_checkpoint(_request(), consent_local_read=True)
    assert exc.value.status_code == 403
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        with pytest.raises(HTTPException) as exc:
            operator_checkpoint(_request(), consent_local_read=False)
    assert exc.value.status_code == 403


def test_unconfigured_does_not_inspect(monkeypatch):
    for name in ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
                 "OWG_M365_GRANT_FILE", "OWG_M365_LEDGER_DIR"):
        monkeypatch.delenv(name, raising=False)
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        response = operator_checkpoint(_request(), consent_local_read=True)
    assert json.loads(response.body)["configured"] is False
    assert response.headers["cache-control"] == "no-store"


def test_scoped_checkpoint_is_redacted(monkeypatch):
    for name, value in (("OWG_M365_TENANT", "private-tenant"),
                        ("OWG_M365_MAILBOX", "private-mailbox"),
                        ("OWG_M365_FOLDER", "private-folder"),
                        ("OWG_M365_GRANT_FILE", "/private/grant.json"),
                        ("OWG_M365_LEDGER_DIR", "/private/ledger")):
        monkeypatch.setenv(name, value)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_checkpoint.inspect_checkpoint",
                return_value={"generation": 3, "cursor_url": "secret-cursor",
                              "log_id": "secret-log"}) as inspect):
        response = operator_checkpoint(_request(), consent_local_read=True)
    body = json.loads(response.body)
    assert body["generation"] == 3
    assert body["checkpoint_present"] is True
    assert body["checkpoint_advanced"] is False
    assert body["provider_validated"] is False
    assert body["automatic_retry_allowed"] is False
    assert all(secret not in response.body.decode() for secret in
               ("private-tenant", "private-mailbox", "secret-cursor", "secret-log"))
    assert inspect.call_args.kwargs["consent_local_read"] is True


def test_revoked_grant_is_not_reported_as_empty(monkeypatch):
    for name in ("OWG_M365_TENANT", "OWG_M365_MAILBOX", "OWG_M365_FOLDER",
                 "OWG_M365_GRANT_FILE", "OWG_M365_LEDGER_DIR"):
        monkeypatch.setenv(name, "configured")
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_checkpoint.inspect_checkpoint",
                side_effect=ValueError("sensitive grant details"))):
        with pytest.raises(HTTPException) as exc:
            operator_checkpoint(_request(), consent_local_read=True)
    assert exc.value.status_code == 409
    assert "sensitive" not in str(exc.value.detail)
