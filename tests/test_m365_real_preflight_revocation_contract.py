"""Production Graph adapter contract: authenticated bounded reads and revocation.

Uses the real identity/folder preflight implementations and a local fake HTTPS
transport. No Microsoft tenant, credentials, or network access are required.
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from server import m365_outlook_mailbox_identity as identity
from server import m365_outlook_folder_preflight as folder


class Response:
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}

    def __init__(self, url, payload):
        self.url = url
        self.payload = json.dumps(payload).encode()

    def geturl(self):
        return self.url

    def read(self, size):
        assert size == 8193  # production bounded response read
        return self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class Transport:
    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    def open(self, req, timeout):
        assert timeout == 15
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://graph.microsoft.com/v1.0/")
        assert req.get_header("Authorization") == "Bearer token"
        self.requests.append(req.full_url)
        return Response(req.full_url, self.payload)


@pytest.mark.parametrize("module,prefix,payload,expected", [
    (identity, "mailbox-identity-", {
        "id": "provider-id", "userPrincipalName": "user@example.org",
        "mail": "user@example.org",
    }, "mailbox_identifier_matched"),
    (folder, "folder-preflight-", {
        "id": "folder-id", "displayName": "Inbox",
        "childFolderCount": 0, "totalItemCount": 2,
    }, "folder_id_matched"),
])
def test_real_graph_preflight_writes_private_evidence_only_after_grant_recheck(
        tmp_path, monkeypatch, module, prefix, payload, expected):
    report = tmp_path / (prefix + "run.json")
    grant = tmp_path / "grant.json"
    grant.write_text("{}")
    checks = []
    transport = Transport(payload)
    monkeypatch.setattr(module, "_scope", lambda *args: "a" * 64)
    monkeypatch.setattr(module, "_delta_private_parent", lambda p: None)
    monkeypatch.setattr(module, "_grant", lambda *args: checks.append("grant"))
    monkeypatch.setattr(module, "_exclusive_private", lambda p, data: Path(p).write_bytes(data))
    args = dict(tenant="tenant", mailbox="mailbox", folder="folder-id",
                grant_file=grant, output=report, token="token",
                consent_graph_read=True, consent_private_report=True,
                opener=transport)
    if module is identity:
        args["expected_address"] = "user@example.org"
    result = module.preflight(**args)
    assert result[expected] is True
    assert len(transport.requests) == 1
    assert len(checks) == 3  # before HTTP, after HTTP, before evidence write
    evidence = json.loads(report.read_text())
    assert evidence[expected] is True
    assert evidence["checkpoint_advanced"] is False
    assert "token" not in report.read_text()


@pytest.mark.parametrize("module,prefix,payload", [
    (identity, "mailbox-identity-", {
        "id": "provider-id", "userPrincipalName": "user@example.org",
    }),
    (folder, "folder-preflight-", {
        "id": "folder-id", "displayName": "Inbox",
        "childFolderCount": 0, "totalItemCount": 2,
    }),
])
def test_real_graph_preflight_revocation_after_http_never_writes_report(
        tmp_path, monkeypatch, module, prefix, payload):
    report = tmp_path / (prefix + "run.json")
    grant = tmp_path / "grant.json"
    grant.write_text("{}")
    calls = []
    monkeypatch.setattr(module, "_scope", lambda *args: "a" * 64)
    monkeypatch.setattr(module, "_delta_private_parent", lambda p: None)

    def grant_check(*args):
        calls.append(1)
        if len(calls) == 2:
            raise PermissionError("revoked")

    monkeypatch.setattr(module, "_grant", grant_check)
    transport = Transport(payload)
    args = dict(tenant="tenant", mailbox="mailbox", folder="folder-id",
                grant_file=grant, output=report, token="token",
                consent_graph_read=True, consent_private_report=True,
                opener=transport)
    if module is identity:
        args["expected_address"] = "user@example.org"
    with pytest.raises((PermissionError, ValueError)):
        module.preflight(**args)
    assert len(transport.requests) == 1
    assert len(calls) == 2
    assert not report.exists()


@pytest.mark.parametrize("module,payload", [
    (identity, {"id": "provider-id", "userPrincipalName": "user@example.org"}),
    (folder, {"id": "folder-id", "displayName": "Inbox",
              "childFolderCount": 0, "totalItemCount": 2}),
])
def test_real_graph_preflight_revoked_before_http_performs_no_provider_io(
        tmp_path, monkeypatch, module, payload):
    """A revoked local grant must stop both Graph reads and evidence creation."""
    report = tmp_path / "report.json"
    grant = tmp_path / "grant.json"
    grant.write_text("{}")
    transport = Transport(payload)
    monkeypatch.setattr(module, "_scope", lambda *args: "a" * 64)
    monkeypatch.setattr(module, "_delta_private_parent", lambda p: None)

    def revoked(*args):
        raise PermissionError("grant revoked")

    monkeypatch.setattr(module, "_grant", revoked)
    args = dict(tenant="tenant", mailbox="mailbox", folder="folder-id",
                grant_file=grant, output=report, token="token",
                consent_graph_read=True, consent_private_report=True,
                opener=transport)
    if module is identity:
        args["expected_address"] = "user@example.org"
    with pytest.raises((PermissionError, ValueError)):
        module.preflight(**args)
    assert transport.requests == []
    assert not report.exists()
