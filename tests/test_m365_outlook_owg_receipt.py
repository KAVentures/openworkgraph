"""Acceptance tests for the real candidate validator and authenticated OWG append path."""
import csv
import hashlib
import io
import json
from pathlib import Path
from urllib import parse

import pytest

from server.m365_outlook_delta import FORMAT as DELTA_FORMAT, _scope, _initial
from server.m365_outlook_owg_receipt import (
    ReceiptError, UncertainReceipt, _artifact, _target, append_verified,
)

LOG = "log_" + "a" * 16
TENANT, MAILBOX, FOLDER = "tenant", "mailbox", "folder"
SCOPE = _scope(TENANT, MAILBOX, FOLDER)
NAMESPACE = "m365_" + SCOPE[:32]


def private(path, contents):
    path.write_bytes(contents)
    path.chmod(0o600)
    return path


def fixture_files(tmp_path):
    tmp_path.chmod(0o700)
    grant = private(tmp_path / "grant.json", json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "tenant_scope": TENANT, "mailbox_scope": MAILBOX,
        "folder_id": FOLDER,
    }).encode())
    csv_bytes = ("case_id,activity,timestamp,source_event_id\n"
                 + "outlookcase:" + "a" * 64
                 + ",Observed read status: read,2026-01-01T00:00:00+00:00,outlookevent:"
                 + "b" * 64 + "\n").encode()
    csv_path = private(tmp_path / "round.csv", csv_bytes)
    candidate = private(tmp_path / "round.json", json.dumps({
        "format": DELTA_FORMAT, "scope_sha256": SCOPE,
        "csv_name": "round.csv", "csv_sha256": hashlib.sha256(csv_bytes).hexdigest(),
        "cursor_url": "https://graph.microsoft.com/v1.0/me/mailFolders/folder/messages/delta?%24deltatoken=opaque",
        "status": "candidate_requires_confirmed_import", "events": 1,
        "removals": 0, "removed_case_ids": [], "pages": 1,
    }).encode())
    return grant, candidate, csv_path


def inventory(events):
    return [{"log_id": LOG, "events": events, "source_format": "csv",
             "source_identity": {"namespaces": [{
                 "source_namespace": NAMESPACE, "case_namespace": NAMESPACE,
             }]}}]


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, url, body):
        self.url, self.body = url, body
    def geturl(self):
        return self.url
    def read(self, limit):
        return self.body[:limit]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class Local:
    def __init__(self, fail_after=False):
        self.events = 3
        self.calls = []
        self.fail_after = fail_after
    def open(self, req, timeout):
        self.calls.append((req.get_method(), req.full_url))
        if req.get_method() == "GET":
            if self.fail_after and len(self.calls) == 3:
                raise OSError("uncertain readback")
            body = {"logs": inventory(self.events)}
        else:
            assert req.get_method() == "POST"
            assert req.headers["Authorization"].startswith("OWG-Session ")
            assert req.headers["X-owg-local-storage-confirmed"] == "true"
            assert parse.parse_qs(parse.urlsplit(req.full_url).query)["source_namespace"] == [NAMESPACE]
            assert req.data.startswith(b"case_id,activity,timestamp,source_event_id")
            self.events += 1
            body = {"log": {"log_id": LOG}, "batch": 2, "added_events": 1,
                    "duplicate_events_skipped": 0, "rejected_events": 0}
        return Response(req.full_url, json.dumps(body).encode())


def options(tmp_path):
    grant, candidate, csv_path = fixture_files(tmp_path)
    return dict(tenant=TENANT, mailbox=MAILBOX, folder=FOLDER,
                grant_file=grant, candidate=candidate, csv_path=csv_path,
                log_id=LOG, confirm_log_id=LOG, port=8765,
                session="s" * 32, consent_local_read=True,
                consent_local_append=True)


def test_real_candidate_and_local_authenticated_append_readback(tmp_path):
    args = options(tmp_path)
    local = Local()
    result = append_verified(**args, opener=local)
    assert result["status"] == "verified"
    assert (result["events_before"], result["events_after"]) == (3, 4)
    assert result["checkpoint_advanced"] is False
    assert result["provider_requests"] == 0
    assert [method for method, _ in local.calls] == ["GET", "POST", "GET"]


def test_refuses_without_consent_or_exact_target_before_network(tmp_path):
    args = options(tmp_path)
    local = Local()
    args["consent_local_append"] = False
    with pytest.raises(ReceiptError):
        append_verified(**args, opener=local)
    assert not local.calls
    args["consent_local_append"] = True
    args["confirm_log_id"] = "log_" + "b" * 16
    with pytest.raises(ReceiptError):
        append_verified(**args, opener=local)
    assert not local.calls


def test_changed_export_refuses_before_network(tmp_path):
    args = options(tmp_path)
    private(args["csv_path"], b"changed")
    local = Local()
    with pytest.raises((ReceiptError, ValueError)):
        append_verified(**args, opener=local)
    assert not local.calls


def test_revoked_grant_refuses_before_network(tmp_path):
    args = options(tmp_path)
    data = json.loads(args["grant_file"].read_text())
    data["enabled"] = False
    private(args["grant_file"], json.dumps(data).encode())
    local = Local()
    with pytest.raises(ValueError):
        append_verified(**args, opener=local)
    assert not local.calls


def test_uncertain_readback_never_retries_post(tmp_path):
    args = options(tmp_path)
    local = Local(fail_after=True)
    with pytest.raises(UncertainReceipt):
        append_verified(**args, opener=local)
    assert [method for method, _ in local.calls].count("POST") == 1


def test_cross_scope_target_refused(tmp_path):
    args = options(tmp_path)
    local = Local()
    local.events = 0
    original = inventory(0)
    original[0]["source_identity"]["namespaces"][0]["source_namespace"] = "other"
    with pytest.raises(ReceiptError):
        _target(original, LOG, NAMESPACE)
