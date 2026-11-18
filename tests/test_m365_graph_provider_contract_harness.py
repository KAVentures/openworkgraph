"""Real Microsoft Graph parser contracts: no live tenant or paid API calls.

Exercises production identity and folder parsers with provider-shaped JSON,
including malformed, ambiguous and unauthorized identity/folder responses.
"""
import json

import pytest

from server.m365_outlook_mailbox_identity import IdentityError, _parse_identity
from server.m365_outlook_folder_preflight import PreflightError, _parse_folder


def payload(value):
    return json.dumps(value, separators=(",", ":")).encode("utf-8")


def test_identity_provider_contract_exact_upn_and_mail():
    provider_id, upn, mail = _parse_identity(payload({
        "id": "provider-user-1", "userPrincipalName": "OWNER@EXAMPLE.ORG",
        "mail": "owner@example.org",
    }), "owner@example.org")
    assert provider_id == "provider-user-1"
    assert upn is True and mail is True


def test_identity_provider_contract_mail_when_upn_differs():
    provider_id, upn, mail = _parse_identity(payload({
        "id": "provider-user-2", "userPrincipalName": "other@example.org",
        "mail": "owner@example.org",
    }), "owner@example.org")
    assert provider_id == "provider-user-2"
    assert upn is False and mail is True


@pytest.mark.parametrize("body", [
    {"id": "user", "userPrincipalName": "wrong@example.org", "mail": "wrong@example.org"},
    {"id": "user", "userPrincipalName": "wrong@example.org"},
    {"id": "user", "userPrincipalName": "owner@example.org", "tenantId": "untrusted"},
    {"id": "user", "userPrincipalName": ["owner@example.org"]},
    {"id": "", "userPrincipalName": "owner@example.org"},
])
def test_identity_provider_contract_refuses_ambiguous_or_mismatched(body):
    with pytest.raises(IdentityError):
        _parse_identity(payload(body), "owner@example.org")


def test_identity_provider_contract_rejects_duplicate_fields():
    with pytest.raises(IdentityError):
        _parse_identity(b'{"id":"one","id":"two","userPrincipalName":"owner@example.org"}',
                        "owner@example.org")


def test_folder_provider_contract_exact_folder():
    children, total = _parse_folder(payload({
        "id": "selected-folder", "displayName": "Inbox",
        "childFolderCount": 0, "totalItemCount": 42,
    }), "selected-folder")
    assert (children, total) == (0, 42)


@pytest.mark.parametrize("body", [
    {"id": "other-folder", "displayName": "Inbox", "childFolderCount": 0, "totalItemCount": 42},
    {"id": "selected-folder", "displayName": "Inbox", "childFolderCount": True, "totalItemCount": 42},
    {"id": "selected-folder", "displayName": "Inbox", "childFolderCount": 0, "totalItemCount": -1},
    {"id": "selected-folder", "displayName": "Inbox", "childFolderCount": 0},
])
def test_folder_provider_contract_refuses_wrong_identity_or_counts(body):
    with pytest.raises(PreflightError):
        _parse_folder(payload(body), "selected-folder")


def test_folder_provider_contract_rejects_duplicate_fields():
    with pytest.raises(PreflightError):
        _parse_folder(b'{"id":"selected-folder","id":"other","displayName":"Inbox",'
                      b'"childFolderCount":0,"totalItemCount":42}', "selected-folder")


def test_real_delta_two_round_http_resume_requires_import_approval(tmp_path):
    """Exercise production Graph transport, durable cursor and explicit resume."""
    from server.m365_outlook_delta import DeltaError, export_delta
    from urllib.parse import quote

    folder = "selected-folder"
    base = "https://graph.microsoft.com/v1.0/me/mailFolders/" + folder + "/messages/delta"
    initial = base + "?%24select=" + quote(
        "id,receivedDateTime,lastModifiedDateTime,isRead", safe="") + "&%24top=25"
    next_page = base + "?%24skiptoken=second-page"
    cursor1 = base + "?%24deltatoken=round-one"
    cursor2 = base + "?%24deltatoken=round-two"
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox", "folder_id": folder,
    }))
    grant.chmod(0o600)

    class Response:
        status = 200
        headers = {"Content-Type": "application/json; charset=utf-8"}

        def __init__(self, url, page):
            self.url, self.data = url, json.dumps(page).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def geturl(self):
            return self.url

        def read(self, limit):
            assert limit == 128 * 1024 + 1
            return self.data[:limit]

    class Transport:
        def __init__(self, pages):
            self.pages, self.requests = pages, []

        def open(self, req, timeout):
            assert timeout == 15 and req.get_method() == "GET"
            assert req.get_header("Authorization") == "Bearer token"
            self.requests.append(req.full_url)
            return Response(req.full_url, self.pages[req.full_url])

    first = Transport({
        initial: {"value": [{
            "id": "message-one", "receivedDateTime": "2026-11-01T10:00:00Z",
            "lastModifiedDateTime": "2026-11-01T10:01:00Z", "isRead": False,
        }], "@odata.nextLink": next_page},
        next_page: {"value": [], "@odata.deltaLink": cursor1},
    })
    args = dict(tenant="tenant", mailbox="mailbox", folder_id=folder,
                grant_file=grant, token="token",
                consent_read=True, consent_export=True)
    csv1, candidate1 = tmp_path / "first.csv", tmp_path / "first.json"
    result = export_delta(**args, output=csv1, candidate=candidate1, opener=first)
    assert first.requests == [initial, next_page]
    assert result["event_snapshots"] == 1
    assert result["checkpoint_advanced"] is False
    assert json.loads(candidate1.read_text())["cursor_url"] == cursor1

    second = Transport({cursor1: {"value": [], "@odata.deltaLink": cursor2}})
    csv2, candidate2 = tmp_path / "second.csv", tmp_path / "second.json"
    with pytest.raises(DeltaError):
        export_delta(**args, output=csv2, candidate=candidate2,
                     previous_candidate=candidate1, opener=second)
    assert second.requests == []
    assert not csv2.exists() and not candidate2.exists()

    result = export_delta(**args, output=csv2, candidate=candidate2,
                          previous_candidate=candidate1,
                          confirm_previous_import=True, opener=second)
    assert second.requests == [cursor1]
    assert result["event_snapshots"] == 0
    assert result["checkpoint_advanced"] is False
    assert json.loads(candidate2.read_text())["cursor_url"] == cursor2
    assert "Bearer token" not in candidate1.read_text() + candidate2.read_text()
