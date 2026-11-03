"""Disposable Graph transport acceptance for selected Outlook metadata export.

No Microsoft account or live provider is contacted. Production request,
grant, projection and private CSV publication paths are exercised.
"""
import csv
import io
import json
import os
from pathlib import Path
from urllib import error

import pytest

from server.m365_outlook_selected import OutlookError, export_selected, main

ID_A = "AAMk+selected/one=="
ID_B = "AAMk+selected/two=="
TOKEN = "secret.token"
TENANT = "local-tenant"
MAILBOX = "reviewed-mailbox"


class Response:
    def __init__(self, req, record=None, *, status=200, final=None, media="application/json"):
        self.status = status
        self._url = req.full_url if final is None else final
        self.headers = {"Content-Type": media}
        self.raw = json.dumps(record if record is not None else {}).encode()

    def geturl(self):
        return self._url

    def read(self, n):
        return self.raw[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Graph:
    def __init__(self, records, *, mutate=None, wrong_url=False, status=200, media="application/json"):
        self.records = records
        self.calls = []
        self.mutate = mutate
        self.wrong_url = wrong_url
        self.status = status
        self.media = media

    def open(self, req, timeout):
        self.calls.append(req)
        assert timeout == 15
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://graph.microsoft.com/v1.0/me/messages/")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert req.get_header("Prefer") == 'IdType="ImmutableId"'
        assert "%24select=id%2CreceivedDateTime%2ClastModifiedDateTime%2CisRead" in req.full_url
        if self.mutate:
            self.mutate(len(self.calls))
        index = len(self.calls) - 1
        return Response(req, self.records[index], status=self.status,
                        final="https://evil.example/redirect" if self.wrong_url else None,
                        media=self.media)


def message(identity, *, read=True, modified="2026-10-30T12:05:00Z"):
    return {
        "id": identity, "receivedDateTime": "2026-10-30T12:00:00Z",
        "lastModifiedDateTime": modified, "isRead": read,
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#users('me')/messages/$entity",
    }


@pytest.fixture
def setup(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    grant = private / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": TENANT, "mailbox_scope": MAILBOX,
        "message_ids": [ID_A, ID_B],
    }))
    grant.chmod(0o600)
    return private, grant


def options(setup, *, output="events.csv"):
    private, grant = setup
    return {
        "tenant": TENANT, "mailbox": MAILBOX,
        "message_ids": [ID_A, ID_B],
        "grant_file": grant, "output": private / output,
        "token": TOKEN, "consent_read": True, "consent_export": True,
    }


def test_real_graph_request_contract_and_private_owg_csv(setup):
    transport = Graph([message(ID_A), message(ID_B, read=False)])
    args = options(setup)
    receipt = export_selected(**args, opener=transport)
    assert receipt["status"] == "exported"
    assert receipt["provider_get_requests"] == 2
    assert receipt["automatic_owg_append"] is False
    assert receipt["checkpoint_advanced"] is False
    assert receipt["historical_completeness"] == "unknown"
    assert len(transport.calls) == 2
    rows = list(csv.DictReader(io.StringIO(args["output"].read_text())))
    assert len(rows) == 2
    assert set(rows[0]) == {"case_id", "activity", "timestamp", "source_event_id"}
    assert {row["activity"] for row in rows} == {
        "Observed read status: read", "Observed read status: unread",
    }
    assert all(row["case_id"].startswith("outlookcase:") for row in rows)
    assert all(row["source_event_id"].startswith("outlookevent:") for row in rows)
    assert ID_A not in args["output"].read_text()
    assert ID_B not in args["output"].read_text()
    assert TOKEN not in args["output"].read_text()
    if os.name == "posix":  # Windows has no POSIX permission bits
        assert os.stat(args["output"]).st_mode & 0o077 == 0
    replay = options(setup, output="replay.csv")
    export_selected(**replay, opener=Graph([message(ID_A), message(ID_B, read=False)]))
    assert replay["output"].read_bytes() == args["output"].read_bytes()
    newer = options(setup, output="newer.csv")
    export_selected(**newer, opener=Graph([
        message(ID_A, modified="2026-10-30T12:06:00Z"), message(ID_B, read=False),
    ]))
    changed = list(csv.DictReader(io.StringIO(newer["output"].read_text())))
    assert next(row["source_event_id"] for row in changed if row["activity"] == "Observed read status: read") != next(row["source_event_id"] for row in rows if row["activity"] == "Observed read status: read")


@pytest.mark.parametrize("failure", [
    "read_consent", "export_consent", "grant_revoked", "wrong_scope",
    "wrong_token", "existing_output",
    pytest.param("unsafe_directory", marks=pytest.mark.skipif(os.name != "posix", reason="POSIX directory permissions do not exist on Windows")),
])
def test_preflight_refuses_without_graph_calls(setup, failure):
    args = options(setup)
    if failure == "read_consent":
        args["consent_read"] = False
    if failure == "export_consent":
        args["consent_export"] = False
    if failure == "grant_revoked":
        value = json.loads(args["grant_file"].read_text())
        value["enabled"] = False
        args["grant_file"].write_text(json.dumps(value))
    if failure == "wrong_scope":
        args["tenant"] = "foreign-tenant"
    if failure == "wrong_token":
        args["token"] = "secret\ninjection"
    if failure == "existing_output":
        args["output"].write_text("DO NOT REPLACE")
    if failure == "unsafe_directory":
        args["output"].parent.chmod(0o755)
    graph = Graph([message(ID_A), message(ID_B)])
    with pytest.raises(OutlookError):
        export_selected(**args, opener=graph)
    assert not graph.calls
    if failure == "existing_output":
        assert args["output"].read_text() == "DO NOT REPLACE"


@pytest.mark.parametrize("failure", [
    "wrong_id", "missing_field", "extra_private", "wrong_type",
    "bad_time", "redirect", "unauthorized", "wrong_media", "duplicate_json",
])
def test_source_failures_never_publish_partial_csv(setup, failure):
    args = options(setup)
    records = [message(ID_A), message(ID_B)]
    settings = {}
    if failure == "wrong_id":
        records[1]["id"] = "foreign-id"
    if failure == "missing_field":
        del records[1]["isRead"]
    if failure == "extra_private":
        records[1]["subject"] = "sensitive"
    if failure == "wrong_type":
        records[1]["isRead"] = "false"
    if failure == "bad_time":
        records[1]["lastModifiedDateTime"] = "not-a-time"
    if failure == "redirect":
        settings["wrong_url"] = True
    if failure == "unauthorized":
        settings["status"] = 403
    if failure == "wrong_media":
        settings["media"] = "text/html"
    if failure == "duplicate_json":
        records[1] = message(ID_B)
    graph = Graph(records, **settings)
    if failure == "duplicate_json":
        original = graph.open

        def duplicate(req, timeout):
            response = original(req, timeout)
            if len(graph.calls) == 2:
                response.raw = b'{"id":"x","id":"y"}'
            return response
        graph.open = duplicate
    with pytest.raises(OutlookError):
        export_selected(**args, opener=graph)
    assert not args["output"].exists()


def test_revocation_between_selected_reads_stops_batch(setup):
    args = options(setup)
    def revoke(n):
        if n == 1:
            value = json.loads(args["grant_file"].read_text())
            value["enabled"] = False
            args["grant_file"].write_text(json.dumps(value))
    graph = Graph([message(ID_A), message(ID_B)], mutate=revoke)
    with pytest.raises(OutlookError):
        export_selected(**args, opener=graph)
    assert len(graph.calls) == 1
    assert not args["output"].exists()


def test_cli_refuses_without_secrets_or_partial_output(setup, monkeypatch, capsys):
    args = options(setup)
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", TOKEN)
    cmd = [
        "--tenant", TENANT, "--mailbox", MAILBOX,
        "--message-id", ID_A, "--message-id", ID_B,
        "--grant-file", str(args["grant_file"]),
        "--output", str(args["output"]),
        "--consent-selected-read", "--consent-local-export",
    ]
    assert main(cmd, opener=Graph([message(ID_A), message(ID_B)])) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["source_snapshots"] == 2
    assert TOKEN not in json.dumps(receipt)
    assert ID_A not in json.dumps(receipt)
    assert MAILBOX not in json.dumps(receipt)
    assert main(cmd, opener=Graph([message(ID_A), message(ID_B)])) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
