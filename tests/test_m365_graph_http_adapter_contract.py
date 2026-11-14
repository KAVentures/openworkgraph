"""Exercise real Graph preflight HTTP adapters without network or tenant credentials.

The fake opener implements the urllib transport boundary, not the production
preflight implementation. Provider status, redirects and malformed responses
must be refused before a private report can be persisted.
"""
import json
from unittest.mock import patch

import pytest

from server import m365_outlook_mailbox_identity as identity
from server import m365_outlook_folder_preflight as folder


class Response:
    def __init__(self, url, body, *, status=200, content_type="application/json"):
        self.url, self.body, self.status = url, body, status
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.body[:limit]


class Transport:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def open(self, req, timeout):
        self.requests.append((req, timeout))
        return self.response


@pytest.mark.parametrize("kind", ["identity", "folder"])
@pytest.mark.parametrize("failure", ["unauthorized", "redirect", "html", "mismatch", "oversized"])
def test_graph_http_adapter_refuses_untrusted_provider_response(tmp_path, kind, failure):
    module = identity if kind == "identity" else folder
    url = (identity.URL if kind == "identity" else
           "https://graph.microsoft.com/v1.0/me/mailFolders/folder?%24select=id,displayName,childFolderCount,totalItemCount")
    data = ({"id": "user", "userPrincipalName": "owner@example.org"} if kind == "identity"
            else {"id": "folder", "displayName": "Inbox", "childFolderCount": 0, "totalItemCount": 2})
    if failure == "mismatch":
        if kind == "identity":
            data["userPrincipalName"] = "other@example.org"
        else:
            data["id"] = "other"
    raw = json.dumps(data).encode()
    if failure == "oversized":
        raw = b"x" * (module.MAX_BYTES + 1)
    response = Response(url + "/else" if failure == "redirect" else url, raw,
                        status=401 if failure == "unauthorized" else 200,
                        content_type="text/html" if failure == "html" else "application/json")
    transport = Transport(response)
    output = tmp_path / ("mailbox-identity-test.json" if kind == "identity"
                         else "folder-preflight-test.json")
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=tmp_path / "grant.json", output=output,
                  token="test-token", consent_graph_read=True,
                  consent_private_report=True, opener=transport)
    if kind == "identity":
        kwargs["expected_address"] = "owner@example.org"
    with (patch.object(module, "_scope", return_value="a" * 64),
          patch.object(module, "_delta_private_parent"),
          patch.object(module, "_grant", return_value={}),
          pytest.raises((identity.IdentityError if kind == "identity" else folder.PreflightError))):
        module.preflight(**kwargs)
    assert not output.exists()
    assert len(transport.requests) == 1
    req, timeout = transport.requests[0]
    assert req.get_method() == "GET" and timeout == 15
    assert req.get_header("Authorization") == "Bearer test-token"
    assert req.full_url == url


@pytest.mark.parametrize("kind", ["identity", "folder"])
def test_graph_http_adapter_writes_private_report_only_after_success(tmp_path, kind):
    module = identity if kind == "identity" else folder
    url = (identity.URL if kind == "identity" else
           "https://graph.microsoft.com/v1.0/me/mailFolders/folder?%24select=id,displayName,childFolderCount,totalItemCount")
    raw = json.dumps({"id": "user", "userPrincipalName": "owner@example.org"} if kind == "identity"
                     else {"id": "folder", "displayName": "Inbox", "childFolderCount": 0,
                           "totalItemCount": 2}).encode()
    output = tmp_path / ("mailbox-identity-test.json" if kind == "identity"
                         else "folder-preflight-test.json")
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=tmp_path / "grant.json", output=output,
                  token="test-token", consent_graph_read=True,
                  consent_private_report=True, opener=Transport(Response(url, raw)))
    if kind == "identity":
        kwargs["expected_address"] = "owner@example.org"
    with (patch.object(module, "_scope", return_value="a" * 64),
          patch.object(module, "_delta_private_parent"),
          patch.object(module, "_grant", return_value={})):
        result = module.preflight(**kwargs)
    assert output.is_file()
    report = json.loads(output.read_text())
    assert report["checkpoint_advanced"] is False
    assert report["owg_import_performed"] is False
    assert report["safe_to_retry"] is False
    assert result["status"] == report["status"]
    assert "test-token" not in output.read_text()


@pytest.mark.parametrize("kind", ["identity", "folder"])
@pytest.mark.parametrize("missing_consent", ["graph", "report"])
def test_graph_http_adapter_requires_both_independent_consents_before_network(
        tmp_path, kind, missing_consent):
    module = identity if kind == "identity" else folder
    output = tmp_path / ("mailbox-identity-consent.json" if kind == "identity"
                         else "folder-preflight-consent.json")
    transport = Transport(None)
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=tmp_path / "grant.json", output=output,
                  token="test-token", consent_graph_read=missing_consent != "graph",
                  consent_private_report=missing_consent != "report",
                  opener=transport)
    if kind == "identity":
        kwargs["expected_address"] = "owner@example.org"
    with pytest.raises(identity.IdentityError if kind == "identity" else folder.PreflightError):
        module.preflight(**kwargs)
    assert transport.requests == []
    assert not output.exists()


@pytest.mark.parametrize("kind", ["identity", "folder"])
def test_graph_http_adapter_revocation_after_response_prevents_report(
        tmp_path, kind):
    """Grant must be checked again after Graph read, before durable evidence."""
    module = identity if kind == "identity" else folder
    url = (identity.URL if kind == "identity" else
           "https://graph.microsoft.com/v1.0/me/mailFolders/folder?%24select=id,displayName,childFolderCount,totalItemCount")
    data = ({"id": "user", "userPrincipalName": "owner@example.org"}
            if kind == "identity" else
            {"id": "folder", "displayName": "Inbox", "childFolderCount": 0,
             "totalItemCount": 2})
    transport = Transport(Response(url, json.dumps(data).encode()))
    output = tmp_path / ("mailbox-identity-revoked.json" if kind == "identity"
                         else "folder-preflight-revoked.json")
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=tmp_path / "grant.json", output=output,
                  token="test-token", consent_graph_read=True,
                  consent_private_report=True, opener=transport)
    if kind == "identity":
        kwargs["expected_address"] = "owner@example.org"
    calls = []

    def grant_check(*args):
        calls.append(args)
        if len(calls) == 2:
            raise PermissionError("revoked secret-token")

    with (patch.object(module, "_scope", return_value="a" * 64),
          patch.object(module, "_delta_private_parent"),
          patch.object(module, "_grant", side_effect=grant_check),
          pytest.raises(PermissionError)):
        module.preflight(**kwargs)
    assert len(calls) == 2
    assert len(transport.requests) == 1
    assert not output.exists()


def test_real_delta_http_adapter_two_complete_rounds_without_implicit_checkpoint(tmp_path):
    """Provider-shaped first and incremental delta pages through the real adapter.

    The first candidate is only a review artifact: the second read requires
    an explicit previous-import confirmation and a SHA-bound CSV on disk.
    """
    from server import m365_outlook_delta as delta

    first_cursor = delta._initial("folder")
    delta_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                    "messages/delta?%24deltatoken=first")
    second_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                     "messages/delta?%24deltatoken=second")
    message = {"id": "message1", "receivedDateTime": "2026-11-01T10:00:00Z",
               "lastModifiedDateTime": "2026-11-01T10:01:00Z", "isRead": False}

    class DeltaTransport:
        def __init__(self, responses):
            self.responses = responses
            self.requests = []

        def open(self, req, timeout):
            self.requests.append((req.full_url, req.get_header("Authorization"), timeout))
            assert req.full_url in self.responses
            return Response(req.full_url, json.dumps(self.responses[req.full_url]).encode())

    first = DeltaTransport({first_cursor: {"value": [message], "@odata.deltaLink": delta_cursor}})
    second = DeltaTransport({delta_cursor: {
        "value": [{"id": "message1", "@removed": {"reason": "deleted"}}],
        "@odata.deltaLink": second_cursor}})
    kwargs = dict(tenant="tenant", mailbox="mailbox", folder_id="folder",
                  grant_file=tmp_path / "grant.json", token="test-token",
                  consent_read=True, consent_export=True)
    with (patch.object(delta, "_delta_private_parent"),
          patch.object(delta, "_grant", return_value=b"fixed-grant")):
        first_result = delta.export_delta(
            **kwargs, output=tmp_path / "first.csv",
            candidate=tmp_path / "first.json", opener=first)
        assert first_result["event_snapshots"] == 1
        assert first_result["checkpoint_advanced"] is False
        assert first_result["automatic_owg_append"] is False
        candidate = json.loads((tmp_path / "first.json").read_text())
        assert candidate["cursor_url"] == delta_cursor
        assert candidate["events"] == 1
        assert candidate["removals"] == 0

        with pytest.raises(delta.DeltaError):
            delta.export_delta(
                **kwargs, output=tmp_path / "refused.csv",
                candidate=tmp_path / "refused.json",
                previous_candidate=tmp_path / "first.json",
                confirm_previous_import=False, opener=second)
        assert second.requests == []
        assert not (tmp_path / "refused.json").exists()

        next_result = delta.export_delta(
            **kwargs, output=tmp_path / "second.csv",
            candidate=tmp_path / "second.json",
            previous_candidate=tmp_path / "first.json",
            confirm_previous_import=True, opener=second)
    assert next_result["event_snapshots"] == 0
    assert next_result["removal_markers_for_review"] == 1
    assert next_result["checkpoint_advanced"] is False
    assert json.loads((tmp_path / "second.json").read_text())["cursor_url"] == second_cursor
    assert len(first.requests) == len(second.requests) == 1
    assert first.requests[0][1:] == second.requests[0][1:] == ("Bearer test-token", 15)


@pytest.mark.parametrize("failure", ["unauthorized", "redirect", "html", "oversized"])
def test_real_delta_http_adapter_refuses_untrusted_transport_without_candidate(tmp_path, failure):
    from server import m365_outlook_delta as delta

    url = delta._initial("folder")
    response = Response(
        url + "/redirect" if failure == "redirect" else url,
        b"x" * (delta.MAX_PAGE_BYTES + 1) if failure == "oversized" else
        json.dumps({"value": [], "@odata.deltaLink":
                    "https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                    "messages/delta?%24deltatoken=first"}).encode(),
        status=401 if failure == "unauthorized" else 200,
        content_type="text/html" if failure == "html" else "application/json")
    transport = Transport(response)
    with (patch.object(delta, "_delta_private_parent"),
          patch.object(delta, "_grant", return_value=b"fixed-grant"),
          pytest.raises(delta.DeltaError)):
        delta.export_delta(
            tenant="tenant", mailbox="mailbox", folder_id="folder",
            grant_file=tmp_path / "grant.json", output=tmp_path / "events.csv",
            candidate=tmp_path / "candidate.json", token="test-token",
            consent_read=True, consent_export=True, opener=transport)
    assert len(transport.requests) == 1
    assert not (tmp_path / "events.csv").exists()
    assert not (tmp_path / "candidate.json").exists()
