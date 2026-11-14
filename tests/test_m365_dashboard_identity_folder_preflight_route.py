"""Dashboard Graph identity/folder preflight is opt-in and fail closed."""
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from server.m365_operator_audit_routes import operator_identity_folder_preflight


def request():
    return Request({"type": "http", "headers": [(b"authorization", b"OWG-Session valid")]})


def configure(monkeypatch):
    for key, value in {
        "TENANT": "tenant", "MAILBOX": "mailbox", "FOLDER": "folder",
        "EXPECTED_ADDRESS": "owner@example.org", "GRANT_FILE": "/private/grant.json",
        "IDENTITY_REPORT": "/private/mailbox-identity-1.json",
        "FOLDER_REPORT": "/private/folder-preflight-1.json",
        "GRAPH_TOKEN": "secret-token",
    }.items():
        monkeypatch.setenv("OWG_M365_" + key, value)


def test_requires_session_and_both_fresh_approvals(monkeypatch):
    configure(monkeypatch)
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=False):
        with pytest.raises(HTTPException) as exc:
            operator_identity_folder_preflight(request(), True, True)
    assert exc.value.status_code == 403
    with patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True):
        for graph, report in [(False, False), (True, False), (False, True)]:
            with pytest.raises(HTTPException) as exc:
                operator_identity_folder_preflight(request(), graph, report)
            assert exc.value.status_code == 403


def test_preflight_calls_real_adapters_without_leaking_identity(monkeypatch):
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_mailbox_identity.preflight", return_value={"status": "mailbox_identity_observed", "mailbox_identifier_matched": True, "tenant_id_verified": False}) as identity,
          patch("server.m365_outlook_folder_preflight.preflight", return_value={"status": "folder_metadata_observed", "folder_id_matched": True}) as folder):
        response = operator_identity_folder_preflight(request(), True, True)
    body = response.body.decode()
    assert response.status_code == 200
    assert '"tenant_verified":false' in body
    assert '"sync_authorized":false' in body
    assert '"automatic_retry_allowed":false' in body
    for secret in ("secret-token", "owner@example.org", "/private/", "tenant\\\":\\\"tenant", "mailbox\\\":\\\"mailbox"):
        assert secret not in body
    assert identity.call_args.kwargs["token"] == "secret-token"
    assert folder.call_args.kwargs["consent_graph_read"] is True


def test_partial_failure_requires_manual_reconciliation_without_retry(monkeypatch):
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_mailbox_identity.preflight", return_value={"status": "mailbox_identity_observed", "mailbox_identifier_matched": True, "tenant_id_verified": False}),
          patch("server.m365_outlook_folder_preflight.preflight", side_effect=ValueError("secret-token revoked")) as folder):
        with pytest.raises(HTTPException) as exc:
            operator_identity_folder_preflight(request(), True, True)
    assert exc.value.status_code == 409
    assert "secret-token" not in str(exc.value.detail)
    assert "Do not retry" in str(exc.value.detail)
    folder.assert_called_once()


def test_missing_token_never_calls_graph(monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("OWG_M365_GRAPH_TOKEN")
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_mailbox_identity.preflight") as identity):
        with pytest.raises(HTTPException) as exc:
            operator_identity_folder_preflight(request(), True, True)
    assert exc.value.status_code == 409
    identity.assert_not_called()


@pytest.mark.parametrize("identity_result,folder_result", [
    ({"status": "mailbox_identity_observed", "mailbox_identifier_matched": False,
      "tenant_id_verified": False}, {"status": "folder_metadata_observed", "folder_id_matched": True}),
    ({"status": "mailbox_identity_observed", "mailbox_identifier_matched": True,
      "tenant_id_verified": True}, {"status": "folder_metadata_observed", "folder_id_matched": True}),
    ({"status": "mailbox_identity_observed", "mailbox_identifier_matched": True,
      "tenant_id_verified": False}, {"status": "folder_metadata_observed", "folder_id_matched": False}),
])
def test_preflight_refuses_contradictory_adapter_evidence(monkeypatch, identity_result, folder_result):
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_mailbox_identity.preflight", return_value=identity_result),
          patch("server.m365_outlook_folder_preflight.preflight", return_value=folder_result)):
        with pytest.raises(HTTPException) as exc:
            operator_identity_folder_preflight(request(), True, True)
    assert exc.value.status_code == 409
    assert "Do not retry" in str(exc.value.detail)


@pytest.mark.parametrize("provider_failure", [
    PermissionError("expired Graph credential: secret-token"),
    OSError("Graph HTTP 401 unauthorized: secret-token"),
    ValueError("revoked local grant /private/grant.json"),
    KeyError("missing mailbox scope"),
])
def test_provider_credential_and_grant_failures_stop_before_folder_read(
        monkeypatch, provider_failure):
    """Never continue Graph reads or expose provider diagnostics after a failed identity check."""
    configure(monkeypatch)
    with (patch("server.m365_operator_audit_routes.dashboard_session_valid", return_value=True),
          patch("server.m365_outlook_mailbox_identity.preflight",
                side_effect=provider_failure) as identity,
          patch("server.m365_outlook_folder_preflight.preflight") as folder):
        with pytest.raises(HTTPException) as exc:
            operator_identity_folder_preflight(request(), True, True)
    assert exc.value.status_code == 409
    assert "Do not retry" in str(exc.value.detail)
    assert "secret-token" not in str(exc.value.detail)
    assert "/private/grant.json" not in str(exc.value.detail)
    identity.assert_called_once()
    folder.assert_not_called()
