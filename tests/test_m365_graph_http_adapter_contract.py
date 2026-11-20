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
@pytest.mark.parametrize("failure", ["unauthorized", "rate_limited", "service_unavailable", "redirect", "html", "malformed_json", "mismatch", "oversized"])
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
    if failure == "malformed_json":
        raw = b"{invalid-json"
    response = Response(url + "/else" if failure == "redirect" else url, raw,
                        status={"unauthorized": 401, "rate_limited": 429,
                                "service_unavailable": 503}.get(failure, 200),
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


def test_delta_private_artifact_reader_rejects_hardlinks(tmp_path):
    """A second link can bypass the expected private artifact ownership boundary."""
    import os
    from server import m365_outlook_delta as delta

    original = tmp_path / "source.csv"
    original.write_bytes(b"private delta evidence")
    os.chmod(original, 0o600)
    linked = tmp_path / "linked.csv"
    os.link(original, linked)
    with pytest.raises(delta.DeltaError, match="Private artifact invalid"):
        delta._private_read(linked, 8192)
    assert original.read_bytes() == b"private delta evidence"


def test_delta_private_artifact_reader_accepts_single_link(tmp_path):
    import os
    from server import m365_outlook_delta as delta

    artifact = tmp_path / "private.csv"
    artifact.write_bytes(b"owned delta evidence")
    os.chmod(artifact, 0o600)
    assert delta._private_read(artifact, 8192) == b"owned delta evidence"


@pytest.mark.parametrize("failure", ["expired", "revoked_during_page"])
def test_real_delta_grant_expiry_and_mid_round_revocation_fail_closed(
        tmp_path, failure):
    """Use the real private grant parser and Graph delta adapter together.

    No cursor or CSV may be published after expiry or revocation, even if
    Graph has already returned a valid first page with a continuation.
    """
    import os
    from server import m365_outlook_delta as delta

    grant_file = tmp_path / "grant.json"
    grant = {
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox",
        "folder_id": "folder",
    }
    if failure == "expired":
        grant["expires_at"] = "2020-01-01T00:00:00Z"

    def write_grant():
        grant_file.write_text(json.dumps(grant))
        os.chmod(grant_file, 0o600)

    write_grant()
    initial = delta._initial("folder")
    continuation = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                    "messages/delta?%24skiptoken=page2")
    terminal = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                "messages/delta?%24deltatoken=done")

    class RevokingTransport:
        def __init__(self):
            self.requests = []

        def open(self, req, timeout):
            self.requests.append(req.full_url)
            assert req.get_header("Authorization") == "Bearer test-token"
            assert timeout == 15
            if len(self.requests) == 1:
                assert req.full_url == initial
                grant["enabled"] = False
                write_grant()
                page = {"value": [], "@odata.nextLink": continuation}
            else:
                assert req.full_url == continuation
                page = {"value": [], "@odata.deltaLink": terminal}
            return Response(req.full_url, json.dumps(page).encode())

    transport = RevokingTransport()
    with pytest.raises(delta.DeltaError):
        delta.export_delta(
            tenant="tenant", mailbox="mailbox", folder_id="folder",
            grant_file=grant_file, output=tmp_path / "events.csv",
            candidate=tmp_path / "candidate.json", token="test-token",
            consent_read=True, consent_export=True, opener=transport)
    assert len(transport.requests) == (0 if failure == "expired" else 1)
    assert not (tmp_path / "events.csv").exists()
    assert not (tmp_path / "candidate.json").exists()


def test_real_graph_delta_candidate_passes_real_offline_review_without_append(tmp_path):
    """Connect the actual Graph HTTP delta adapter to the actual candidate reviewer.

    This covers the provider-to-review boundary with persisted artifacts; it
    intentionally does not claim OWG append or checkpoint integration.
    """
    from server import m365_outlook_delta as delta
    from server import m365_outlook_candidate_review as reviewer

    url = delta._initial("folder")
    cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
              "messages/delta?%24deltatoken=review")
    response = Response(url, json.dumps({
        "value": [{"id": "message1",
                   "receivedDateTime": "2026-11-01T10:00:00Z",
                   "lastModifiedDateTime": "2026-11-01T10:01:00Z",
                   "isRead": False}],
        "@odata.deltaLink": cursor,
    }).encode())
    transport = Transport(response)
    candidate = tmp_path / "review.json"
    csv_path = tmp_path / "review.csv"
    scope = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                 grant_file=tmp_path / "grant.json")
    with (patch.object(delta, "_delta_private_parent"),
          patch.object(delta, "_grant", return_value=b"fixed-grant"),
          patch.object(reviewer, "_delta_private_parent"),
          patch.object(reviewer, "_grant", return_value=b"fixed-grant")):
        exported = delta.export_delta(
            tenant=scope["tenant"], mailbox=scope["mailbox"],
            folder_id=scope["folder"], grant_file=scope["grant_file"],
            token="test-token", consent_read=True, consent_export=True,
            output=csv_path, candidate=candidate, opener=transport)
        reviewed = reviewer.review(
            **scope, candidate=candidate, consent_local_read=True)

    assert reviewed["status"] == "review_ready"
    assert reviewed["csv_sha256"] == json.loads(candidate.read_text())["csv_sha256"]
    assert reviewed["event_snapshots"] == exported["event_snapshots"] == 1
    assert reviewed["removal_markers_for_review"] == 0
    assert reviewed["owg_writes"] == 0
    assert reviewed["checkpoint_advanced"] is False
    assert exported["checkpoint_advanced"] is False
    assert len(transport.requests) == 1
    assert transport.requests[0][0].get_header("Authorization") == "Bearer test-token"


def test_real_graph_candidate_review_and_transaction_preflight_bridge(tmp_path):
    """Real Graph HTTP -> private CSV/candidate -> review -> transaction preflight.

    The checkpoint inspection is isolated because this is deliberately a
    pre-append contract; no OWG writes or checkpoint mutation are authorized.
    """
    from server import m365_outlook_delta as delta
    from server.m365_outlook_candidate_review import review
    from server.m365_outlook_reviewed_transaction import preflight

    grant_file = tmp_path / "grant.json"
    grant_file.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox",
        "folder_id": "folder",
    }))
    grant_file.chmod(0o600)
    initial = delta._initial("folder")
    cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
              "messages/delta?%24deltatoken=reviewed")
    message = {
        "id": "message-1",
        "receivedDateTime": "2026-11-01T10:00:00Z",
        "lastModifiedDateTime": "2026-11-01T10:01:00Z",
        "isRead": False,
    }
    transport = Transport(Response(initial, json.dumps({
        "value": [message], "@odata.deltaLink": cursor,
    }).encode()))
    csv_path = tmp_path / "round.csv"
    candidate = tmp_path / "round.json"
    scope = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                 grant_file=grant_file)
    delta.export_delta(
        tenant="tenant", mailbox="mailbox", folder_id="folder",
        grant_file=grant_file, output=csv_path, candidate=candidate,
        token="test-token", consent_read=True, consent_export=True,
        opener=transport,
    )
    reviewed = review(**scope, candidate=candidate, consent_local_read=True)
    assert reviewed["status"] == "review_ready"
    assert reviewed["event_snapshots"] == 1
    assert reviewed["checkpoint_advanced"] is False
    assert reviewed["csv_sha256"] == json.loads(candidate.read_text())["csv_sha256"]

    with patch("server.m365_outlook_reviewed_transaction.inspect_checkpoint",
               return_value=None) as checkpoint:
        proposed = preflight(
            **scope, log_id="log", confirm_log_id="log",
            candidate=candidate, ledger_dir=tmp_path / "ledger",
            expected_review_csv_sha256=reviewed["csv_sha256"],
            consent_local_read=True,
        )
    assert checkpoint.call_count == 2
    assert proposed["status"] == "review_ready_for_explicit_commit"
    assert proposed["proposed_generation"] == 1
    assert proposed["reviewed_event_snapshots"] == 1
    assert proposed["owg_writes"] == 0
    assert proposed["checkpoint_advanced"] is False
    assert len(transport.requests) == 1
    assert "test-token" not in candidate.read_text()


def test_graph_review_real_durable_checkpoint_two_round_restart(tmp_path):
    """Graph transport -> private review -> real CAS ledger, then incremental Graph.

    OWG append/membership and handoff are controlled adapter boundaries here,
    NOT proof of an authenticated OWG write or live Microsoft tenant.
    """
    import os
    from server import m365_outlook_delta as delta
    from server.m365_outlook_candidate_review import review
    from server import m365_outlook_checkpoint as checkpoint

    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox",
        "folder_id": "folder",
    }))
    grant.chmod(0o600)
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    scope = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                 grant_file=grant)
    first_url = delta._initial("folder")
    first_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                    "messages/delta?%24deltatoken=one")
    second_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                     "messages/delta?%24deltatoken=two")
    message = {"id": "m1", "receivedDateTime": "2026-11-01T10:00:00Z",
               "lastModifiedDateTime": "2026-11-01T10:01:00Z",
               "isRead": False}
    rounds = [
        (first_url, {"value": [message], "@odata.deltaLink": first_cursor}),
        (first_cursor, {"value": [], "@odata.deltaLink": second_cursor}),
    ]
    predecessor = None
    for number, (url, page) in enumerate(rounds, 1):
        transport = Transport(Response(url, json.dumps(page).encode()))
        csv_path = tmp_path / f"round{number}.csv"
        candidate = tmp_path / f"round{number}.json"
        delta.export_delta(
            tenant="tenant", mailbox="mailbox", folder_id="folder",
            grant_file=grant, output=csv_path, candidate=candidate,
            token="test-token", consent_read=True, consent_export=True,
            opener=transport, **({
                "previous_candidate": tmp_path / "round1.json",
                "confirm_previous_import": True,
            } if number == 2 else {}),
        )
        reviewed = review(**scope, candidate=candidate, consent_local_read=True)
        assert reviewed["status"] == "review_ready"
        assert reviewed["event_snapshots"] == (1 if number == 1 else 0)
        assert len(transport.requests) == 1
        assert transport.requests[0][0].full_url == url
        assert checkpoint.inspect_checkpoint(
            **scope, ledger_dir=ledger, consent_local_read=True
        ) == (None if number == 1 else first_entry)
        with (
            patch.object(checkpoint, "prepare_handoff",
                         return_value={"idempotent_replay": True}) as handoff,
            patch.object(checkpoint, "reconcile_membership",
                         return_value={"status": "all_candidate_events_present"}) as membership,
        ):
            committed = checkpoint.commit_checkpoint(
                **scope, log_id="log", confirm_log_id="log",
                candidate=candidate, csv_path=csv_path,
                receipt_file=tmp_path / "receipt.json",
                journal_dir=tmp_path / "journal",
                handoff_dir=tmp_path / "handoff",
                successor_label="next", ledger_dir=ledger,
                expected_previous_candidate_sha256=predecessor,
                consent_local_read=True, confirm_removals_reviewed=True,
                confirm_checkpoint_commit=True,
            )
        handoff.assert_called_once()
        membership.assert_called_once()
        assert committed["generation"] == number
        assert committed["idempotent_replay"] is False
        # Reopen from disk as a new inspection, not a cached in-memory cursor.
        entry = checkpoint.inspect_checkpoint(
            **scope, ledger_dir=ledger, consent_local_read=True)
        assert entry["generation"] == number
        assert entry["candidate_sha256"] == reviewed["csv_sha256"]
        assert entry["cursor_url"] == (first_cursor if number == 1 else second_cursor)
        assert entry["event_membership_verified"] is True
        assert "test-token" not in "".join(p.read_text() for p in ledger.iterdir())
        if number == 1:
            first_entry = entry
        predecessor = reviewed["csv_sha256"]

    # Revocation must hide the durable cursor even though the ledger persists.
    grant.write_text(json.dumps({
        "version": 1, "enabled": False, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox",
        "folder_id": "folder",
    }))
    os.chmod(grant, 0o600)
    with pytest.raises(delta.DeltaError):
        checkpoint.inspect_checkpoint(
            **scope, ledger_dir=ledger, consent_local_read=True)
    assert len(list(ledger.iterdir())) == 2


def test_real_graph_review_to_authenticated_owg_append_contract(tmp_path):
    """Exercise actual Graph CSV and OWG append adapters across their HTTP boundary.

    Only the HTTP transports and OWG inventory responses are simulated. No
    Microsoft tenant or running OWG daemon is needed; checkpoint is untouched.
    """
    import os
    from server import m365_outlook_delta as delta
    from server.m365_outlook_candidate_review import review
    from server import m365_outlook_owg_receipt as owg

    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": "tenant", "mailbox_scope": "mailbox",
        "folder_id": "folder",
    }))
    os.chmod(grant, 0o600)
    initial = delta._initial("folder")
    cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
              "messages/delta?%24deltatoken=append")
    transport = Transport(Response(initial, json.dumps({
        "value": [{"id": "m1", "receivedDateTime": "2026-11-01T10:00:00Z",
                   "lastModifiedDateTime": "2026-11-01T10:01:00Z",
                   "isRead": False}],
        "@odata.deltaLink": cursor,
    }).encode()))
    csv_path, candidate = tmp_path / "events.csv", tmp_path / "candidate.json"
    delta.export_delta(
        tenant="tenant", mailbox="mailbox", folder_id="folder",
        grant_file=grant, output=csv_path, candidate=candidate,
        token="test-token", consent_read=True, consent_export=True,
        opener=transport,
    )
    reviewed = review(
        tenant="tenant", mailbox="mailbox", folder="folder",
        grant_file=grant, candidate=candidate, consent_local_read=True,
    )
    assert reviewed["status"] == "review_ready"
    assert reviewed["event_snapshots"] == 1

    scope = delta._scope("tenant", "mailbox", "folder")
    namespace = "m365_" + scope[:32]
    log_id = "log_" + "a" * 16
    inventory = lambda count: [{
        "log_id": log_id, "source_format": "csv", "events": count,
        "source_identity": {"namespaces": [{
            "source_namespace": namespace, "case_namespace": namespace,
        }]},
    }]
    seen = []
    def post_response(_transport, req):
        seen.append(req)
        assert req.get_method() == "POST"
        assert req.full_url.startswith("http://127.0.0.1:8765/v1/event-logs/" + log_id + "/append?")
        assert req.get_header("Authorization") == "OWG-Session " + "s" * 24
        assert req.get_header("X-owg-local-storage-confirmed") == "true"
        assert req.data == csv_path.read_bytes()
        return {"log": {"log_id": log_id}, "added_events": 1,
                "duplicate_events_skipped": 0, "rejected_events": 0, "batch": 1}

    with (patch.object(owg, "_inventory", side_effect=[inventory(0), inventory(1)]) as reads,
          patch.object(owg, "_read_json", side_effect=post_response)):
        receipt = owg.append_verified(
            tenant="tenant", mailbox="mailbox", folder="folder",
            grant_file=grant, candidate=candidate, csv_path=csv_path,
            log_id=log_id, confirm_log_id=log_id, port=8765,
            session="s" * 24, consent_local_read=True,
            consent_local_append=True, opener=object(),
        )
    assert reads.call_count == 2
    assert len(seen) == 1
    assert receipt["status"] == "verified"
    assert receipt["added_events"] == 1
    assert receipt["csv_sha256"] == reviewed["csv_sha256"]
    assert receipt["checkpoint_advanced"] is False
    assert len(transport.requests) == 1

    # Continue this same provider -> review -> authenticated append journey
    # through the actual durable checkpoint writer, then restart from disk.
    from server import m365_outlook_checkpoint as checkpoint
    ledger = tmp_path / "ledger"
    ledger.mkdir(mode=0o700)
    scope_args = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                      grant_file=grant)
    assert checkpoint.inspect_checkpoint(
        **scope_args, ledger_dir=ledger, consent_local_read=True) is None
    with (patch.object(checkpoint, "prepare_handoff",
                       return_value={"idempotent_replay": True}),
          patch.object(checkpoint, "reconcile_membership",
                       return_value={"status": "all_candidate_events_present"})):
        committed = checkpoint.commit_checkpoint(
            **scope_args, log_id=log_id, confirm_log_id=log_id,
            candidate=candidate, csv_path=csv_path,
            receipt_file=tmp_path / "receipt.json",
            journal_dir=tmp_path / "journal",
            handoff_dir=tmp_path / "handoff",
            successor_label="incremental", ledger_dir=ledger,
            consent_local_read=True, confirm_removals_reviewed=True,
            confirm_checkpoint_commit=True)
    assert committed["generation"] == 1
    persisted = checkpoint.inspect_checkpoint(
        **scope_args, ledger_dir=ledger, consent_local_read=True)
    assert persisted["candidate_sha256"] == reviewed["csv_sha256"]
    assert persisted["cursor_url"] == cursor

    # A fresh adapter instance must use the durable cursor, not the initial
    # Graph URL; the second candidate remains uncommitted until review.
    next_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                   "messages/delta?%24deltatoken=incremental")
    incremental = Transport(Response(cursor, json.dumps({
        "value": [], "@odata.deltaLink": next_cursor,
    }).encode()))
    next_csv, next_candidate = tmp_path / "next.csv", tmp_path / "next.json"
    delta.export_delta(
        tenant="tenant", mailbox="mailbox", folder_id="folder",
        grant_file=grant, output=next_csv, candidate=next_candidate,
        token="test-token", consent_read=True, consent_export=True,
        previous_candidate=candidate, confirm_previous_import=True,
        opener=incremental)
    assert len(incremental.requests) == 1
    assert incremental.requests[0][0].full_url == persisted["cursor_url"]
    assert review(**scope_args, candidate=next_candidate,
                  consent_local_read=True)["status"] == "review_ready"
    assert checkpoint.inspect_checkpoint(
        **scope_args, ledger_dir=ledger,
        consent_local_read=True)["generation"] == 1

    # Explicitly commit the incremental zero-event round, proving that a
    # second reviewed cursor can advance without inventing OWG events.
    second_review = review(
        **scope_args, candidate=next_candidate, consent_local_read=True)
    assert second_review["event_snapshots"] == 0
    with (patch.object(checkpoint, "prepare_handoff",
                       return_value={"idempotent_replay": True}) as handoff,
          patch.object(checkpoint, "reconcile_membership",
                       return_value={"status": "all_candidate_events_present"}) as membership):
        second_commit = checkpoint.commit_checkpoint(
            **scope_args, log_id=log_id, confirm_log_id=log_id,
            candidate=next_candidate, csv_path=next_csv,
            receipt_file=tmp_path / "second-receipt.json",
            journal_dir=tmp_path / "second-journal",
            handoff_dir=tmp_path / "second-handoff",
            successor_label="next-incremental", ledger_dir=ledger,
            expected_previous_candidate_sha256=reviewed["csv_sha256"],
            consent_local_read=True, confirm_removals_reviewed=True,
            confirm_checkpoint_commit=True)
    handoff.assert_called_once()
    membership.assert_called_once()
    assert second_commit["generation"] == 2
    reloaded = checkpoint.inspect_checkpoint(
        **scope_args, ledger_dir=ledger, consent_local_read=True)
    assert reloaded["generation"] == 2
    assert reloaded["cursor_url"] == next_cursor
    assert reloaded["candidate_sha256"] == second_review["csv_sha256"]
    assert len(list(ledger.glob("checkpoint-*.json"))) == 2


def test_real_graph_adapter_two_rounds_each_require_fresh_candidate_review(tmp_path):
    """Run real HTTP delta and real reviewer twice, preserving SHA-bound evidence.

    Only the HTTP transport and private grant lookup are simulated. Neither
    review authorizes an OWG append or advances a durable checkpoint.
    """
    from server import m365_outlook_delta as delta
    from server import m365_outlook_candidate_review as reviewer

    first_url = delta._initial("folder")
    first_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                    "messages/delta?%24deltatoken=first")
    second_cursor = ("https://graph.microsoft.com/v1.0/me/mailFolders/folder/"
                     "messages/delta?%24deltatoken=second")
    responses = {
        first_url: {"value": [
            {"id": "message1", "receivedDateTime": "2026-11-01T10:00:00Z",
             "lastModifiedDateTime": "2026-11-01T10:01:00Z", "isRead": False}],
            "@odata.deltaLink": first_cursor},
        first_cursor: {"value": [
            {"id": "message1", "@removed": {"reason": "deleted"}}],
            "@odata.deltaLink": second_cursor},
    }

    class TwoRoundTransport:
        def __init__(self):
            self.requests = []

        def open(self, req, timeout):
            self.requests.append(req.full_url)
            assert req.get_header("Authorization") == "Bearer test-token"
            assert timeout == 15
            return Response(req.full_url, json.dumps(responses[req.full_url]).encode())

    transport = TwoRoundTransport()
    common = dict(tenant="tenant", mailbox="mailbox", folder="folder",
                  grant_file=tmp_path / "grant.json")
    with (patch.object(delta, "_delta_private_parent"),
          patch.object(delta, "_grant", return_value=b"fixed-grant"),
          patch.object(reviewer, "_delta_private_parent"),
          patch.object(reviewer, "_grant", return_value=b"fixed-grant")):
        for round_number, expected_events, expected_removals in ((1, 1, 0), (2, 0, 1)):
            csv_path = tmp_path / f"round{round_number}.csv"
            candidate = tmp_path / f"round{round_number}.json"
            previous = tmp_path / "round1.json" if round_number == 2 else None
            exported = delta.export_delta(
                tenant="tenant", mailbox="mailbox", folder_id="folder",
                grant_file=common["grant_file"], token="test-token",
                consent_read=True, consent_export=True,
                output=csv_path, candidate=candidate,
                previous_candidate=previous,
                confirm_previous_import=round_number == 2, opener=transport)
            reviewed = reviewer.review(
                **common, candidate=candidate, consent_local_read=True)
            record = json.loads(candidate.read_text())
            assert reviewed["status"] == "review_ready"
            assert reviewed["csv_sha256"] == record["csv_sha256"]
            assert reviewed["event_snapshots"] == exported["event_snapshots"] == expected_events
            assert reviewed["removal_markers_for_review"] == expected_removals
            assert reviewed["owg_writes"] == 0
            assert reviewed["checkpoint_advanced"] is False
            assert exported["checkpoint_advanced"] is False
    assert transport.requests == [first_url, first_cursor]
    assert json.loads((tmp_path / "round2.json").read_text())["cursor_url"] == second_cursor
