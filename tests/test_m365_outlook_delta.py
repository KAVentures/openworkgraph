"""Provider-shaped Graph delta HTTP acceptance; no real Microsoft tenant accessed."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path

import pytest

from server.m365_outlook_delta import DeltaError, export_delta, main

TENANT = "reviewed-tenant"
MAILBOX = "reviewed-mailbox"
FOLDER = "AQMk+folder/id=="
ID_A = "AAMk+message/one=="
ID_B = "AAMk+message/two=="
TOKEN = "secret.token"
BASE = ("https://graph.microsoft.com/v1.0/me/mailFolders/"
        "AQMk%2Bfolder%2Fid%3D%3D/messages/delta")
NEXT = BASE + "?%24skiptoken=page-2"
DONE = BASE + "?%24deltatoken=finished"


class Response:
    def __init__(self, req, page, *, status=200, final=None, media="application/json"):
        self.status = status
        self.headers = {"Content-Type": media}
        self._url = req.full_url if final is None else final
        self._body = json.dumps(page).encode()

    def geturl(self):
        return self._url

    def read(self, n):
        return self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Graph:
    def __init__(self, pages, *, mutate=None, status=200, final=None, media="application/json"):
        self.pages = pages
        self.calls = []
        self.mutate = mutate
        self.status = status
        self.final = final
        self.media = media

    def open(self, req, timeout):
        self.calls.append(req)
        assert timeout == 15
        assert req.get_method() == "GET"
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert 'IdType="ImmutableId"' in req.get_header("Prefer")
        assert "odata.maxpagesize=25" in req.get_header("Prefer")
        assert req.full_url.startswith(BASE)
        if self.mutate:
            self.mutate(len(self.calls))
        return Response(req, self.pages[len(self.calls) - 1],
                        status=self.status, final=self.final, media=self.media)


def message(identity, *, read=False, modified="2026-11-02T13:00:00Z"):
    return {
        "id": identity, "receivedDateTime": "2026-11-02T12:00:00Z",
        "lastModifiedDateTime": modified, "isRead": read,
        "@odata.etag": 'W/"opaque"',
    }


def page(values, *, link=DONE, next_page=False):
    return {"value": values,
            "@odata.nextLink" if next_page else "@odata.deltaLink": link}


@pytest.fixture
def private(tmp_path):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    grant = parent / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": TENANT, "mailbox_scope": MAILBOX, "folder_id": FOLDER,
    }))
    grant.chmod(0o600)
    return parent, grant


def args(private, stem="first"):
    parent, grant = private
    return {
        "tenant": TENANT, "mailbox": MAILBOX, "folder_id": FOLDER,
        "grant_file": grant, "output": parent / (stem + ".csv"),
        "candidate": parent / (stem + ".json"), "token": TOKEN,
        "consent_read": True, "consent_export": True,
    }


def test_complete_two_page_round_exports_hashed_events_and_removal_review(private):
    params = args(private)
    transport = Graph([
        page([message(ID_A)], link=NEXT, next_page=True),
        page([message(ID_B, read=True), {"id": "AAMk+deleted==",
                                         "@removed": {"reason": "deleted"}}]),
    ])
    receipt = export_delta(**params, opener=transport)
    assert receipt["status"] == "review_ready"
    assert receipt["provider_get_requests"] == 2
    assert receipt["source_rows"] == 3
    assert receipt["event_snapshots"] == 2
    assert receipt["removal_markers_for_review"] == 1
    assert receipt["checkpoint_advanced"] is False
    assert receipt["automatic_owg_append"] is False
    assert len(transport.calls) == 2
    assert "%24select=" in transport.calls[0].full_url
    assert transport.calls[1].full_url == NEXT
    body = params["output"].read_bytes()
    rows = list(csv.DictReader(io.StringIO(body.decode())))
    assert len(rows) == 2
    assert set(rows[0]) == {"case_id", "activity", "timestamp", "source_event_id"}
    assert all(r["case_id"].startswith("outlookcase:") for r in rows)
    assert all(r["source_event_id"].startswith("outlookevent:") for r in rows)
    assert all(r["timestamp"].endswith("+00:00") for r in rows)
    manifest = json.loads(params["candidate"].read_text())
    assert manifest["csv_sha256"] == hashlib.sha256(body).hexdigest()
    assert manifest["cursor_url"] == DONE
    assert manifest["status"] == "candidate_requires_confirmed_import"
    assert manifest["removals"] == 1
    assert len(manifest["removed_case_ids"]) == 1
    assert manifest["removed_case_ids"][0].startswith("outlookcase:")
    for artifact in (params["output"], params["candidate"]):
        assert os.stat(artifact).st_mode & 0o077 == 0
        assert TOKEN not in artifact.read_text()
        assert ID_A not in artifact.read_text()
        assert ID_B not in artifact.read_text()
        assert "AAMk+deleted==" not in artifact.read_text()


def test_confirmed_prior_import_resumes_exact_delta_cursor(private):
    first = args(private)
    export_delta(**first, opener=Graph([page([message(ID_A)])]))
    second = args(private, "second")
    second.update(previous_candidate=first["candidate"], confirm_previous_import=True)
    transport = Graph([page([])])
    receipt = export_delta(**second, opener=transport)
    assert receipt["event_snapshots"] == 0
    assert transport.calls[0].full_url == DONE
    assert list(csv.DictReader(io.StringIO(second["output"].read_text()))) == []
    third = args(private, "third")
    third.update(previous_candidate=first["candidate"], confirm_previous_import=False)
    no_calls = Graph([page([])])
    with pytest.raises(DeltaError):
        export_delta(**third, opener=no_calls)
    assert not no_calls.calls


@pytest.mark.parametrize("failure", [
    "read_consent", "export_consent", "revoked", "wrong_scope",
    "existing_csv", "existing_candidate", "bad_token", "unsafe_dir",
    "unconfirmed_resume", "tampered_prior_csv", "wrong_folder",
])
def test_preflight_refuses_without_provider_request(private, failure):
    params = args(private)
    if failure == "read_consent":
        params["consent_read"] = False
    elif failure == "export_consent":
        params["consent_export"] = False
    elif failure == "revoked":
        data = json.loads(params["grant_file"].read_text())
        data["enabled"] = False
        params["grant_file"].write_text(json.dumps(data))
    elif failure == "wrong_scope":
        params["tenant"] = "foreign"
    elif failure == "existing_csv":
        params["output"].write_text("KEEP")
    elif failure == "existing_candidate":
        params["candidate"].write_text("KEEP")
    elif failure == "bad_token":
        params["token"] = "secret\nleak"
    elif failure == "unsafe_dir":
        params["output"].parent.chmod(0o755)
    elif failure in ("unconfirmed_resume", "tampered_prior_csv"):
        prior = args(private, "prior")
        export_delta(**prior, opener=Graph([page([message(ID_A)])]))
        params["previous_candidate"] = prior["candidate"]
        params["confirm_previous_import"] = failure == "tampered_prior_csv"
        if failure == "tampered_prior_csv":
            prior["output"].write_text("TAMPERED")
    elif failure == "wrong_folder":
        params["folder_id"] = "foreign-folder"
    graph = Graph([page([message(ID_A)])])
    with pytest.raises(DeltaError):
        export_delta(**params, opener=graph)
    assert not graph.calls
    if failure == "existing_csv":
        assert params["output"].read_text() == "KEEP"


@pytest.mark.parametrize("failure", [
    "unsafe_host", "foreign_folder", "loop", "partial", "oversized",
    "private_field", "bad_removal", "missing_modified", "changed_identity",
    "conflicting_state", "redirect", "bad_media", "bad_status",
])
def test_provider_failures_never_publish_candidate(private, failure):
    params = args(private)
    values = [message(ID_A)]
    first = page(values)
    second = None
    options = {}
    if failure == "unsafe_host":
        first = page(values, link="https://evil.example/steal?$deltatoken=secret")
    elif failure == "foreign_folder":
        first = page(values, link=DONE.replace("folder", "other"))
    elif failure == "loop":
        first = page(values, link=NEXT, next_page=True)
        second = page(values, link=NEXT, next_page=True)
    elif failure == "partial":
        first = page(values, link=NEXT, next_page=True)
        second = page(values, link=NEXT, next_page=True)
    elif failure == "oversized":
        first = page([message(f"msg{i}") for i in range(26)])
    elif failure == "private_field":
        values[0]["subject"] = "private"
    elif failure == "bad_removal":
        first = page([{"id": ID_A, "@removed": {"reason": "unknown"}}])
    elif failure == "missing_modified":
        del values[0]["lastModifiedDateTime"]
    elif failure == "changed_identity":
        values[0]["id"] = "invalid id"
    elif failure == "conflicting_state":
        first = page([message(ID_A), message(ID_A, read=True)])
    elif failure == "redirect":
        options["final"] = "https://evil.example/redirect"
    elif failure == "bad_media":
        options["media"] = "text/html"
    elif failure == "bad_status":
        options["status"] = 403
    graph = Graph([first] if second is None else [first, second], **options)
    with pytest.raises(DeltaError):
        export_delta(**params, opener=graph)
    assert not params["candidate"].exists()
    assert not params["output"].exists()


def test_revocation_after_first_page_refuses_second_get(private):
    params = args(private)
    def revoke(n):
        if n == 1:
            data = json.loads(params["grant_file"].read_text())
            data["enabled"] = False
            params["grant_file"].write_text(json.dumps(data))
    graph = Graph([page([message(ID_A)], link=NEXT, next_page=True)], mutate=revoke)
    with pytest.raises(DeltaError):
        export_delta(**params, opener=graph)
    assert len(graph.calls) == 1
    assert not params["candidate"].exists()


def test_cli_explicit_consents_and_minimized_receipt(private, monkeypatch, capsys):
    params = args(private)
    monkeypatch.setenv("OWG_M365_GRAPH_TOKEN", TOKEN)
    cmd = [
        "--tenant", TENANT, "--mailbox", MAILBOX, "--folder-id", FOLDER,
        "--grant-file", str(params["grant_file"]),
        "--output", str(params["output"]), "--candidate", str(params["candidate"]),
        "--consent-delta-read", "--consent-local-export",
    ]
    assert main(cmd, opener=Graph([page([message(ID_A)])])) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "review_ready"
    assert TOKEN not in output and FOLDER not in output and ID_A not in output
    assert main(cmd, opener=Graph([page([message(ID_A)])])) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
