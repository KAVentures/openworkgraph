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
