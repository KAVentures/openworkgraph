"""End-to-end synthetic ServiceNow HTTPS -> journal -> real local OWG HTTP tests.

The ServiceNow transport is fake. The OWG side uses a loopback HTTP server
and the actual event-log storage/append engine. No external provider calls.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
from threading import Thread
from urllib import parse

import pytest

from server import event_log_workspace as workspace
from server.servicenow_incident_journal import _export_csv, _scope_digest
from server.servicenow_incident_staging import SelectedIncidents
from server.servicenow_incident_run import main

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
TOKEN = "FAKE-PROVIDER-TOKEN-NOT-REAL"
SESSION = "FAKE-LOCAL-SESSION-NOT-REAL"


class ProviderResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, url, record, status=200):
        self.url, self.record, self.status = url, record, status

    def geturl(self):
        return self.url

    def read(self, size):
        return json.dumps({"result": [self.record]}).encode()[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Provider:
    def __init__(self, *, status=200):
        self.calls = 0
        self.revision = 3
        self.status = status

    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://" + HOST + "/api/now/table/incident?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout == 15
        self.calls += 1
        return ProviderResponse(req.full_url, {
            "sys_id": INCIDENT, "sys_mod_count": str(self.revision),
            "sys_updated_on": "2026-10-30 03:10:00",
            "state": str(self.revision), "short_description": "PRIVATE-CONTENT",
        }, status=self.status)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": "team-a", "sys_ids": [INCIDENT],
    }))
    if os.name == "posix":
        grant.chmod(0o600)
    scope = SelectedIncidents(
        host=HOST, tenant_scope="team-a", sys_ids=(INCIDENT,),
        consented=True, grant_current=True,
    )
    namespace = "snow_" + _scope_digest(scope, "UTC")[:32]
    mapping = {
        "case_column": "case_id", "activity_column": "activity",
        "timestamp_column": "timestamp", "event_id_column": "source_event_id",
        "source_namespace": namespace, "case_namespace": namespace,
    }
    old = (
        "snowcase:" + hashlib.sha256(b"baseline").hexdigest(),
        "Incident state: baseline", "2026-10-30T00:00:00+00:00",
        "snowevent:" + hashlib.sha256(b"baseline").hexdigest(),
    )
    made = workspace.import_log(
        BytesIO(_export_csv([old]).encode()), name="Selected ServiceNow",
        source_format="csv", mapping=mapping,
    )
    return tmp_path, grant, made["log_id"], mapping


def cli(env, *, output="recovery-1.csv", consent=True, tenant="team-a",
        log_id=None, port=8765):
    root, grant, saved_log, _ = env
    target = saved_log if log_id is None else log_id
    args = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(root / "journal.sqlite"),
        "--output", str(root / output),
        "--log-id", target, "--confirm-log-id", target,
        "--port", str(port),
    ]
    if consent:
        args += [
            "--consent-selected-read", "--consent-local-journal-read",
            "--consent-local-append",
        ]
    return args


@contextmanager
def owg_server(mapping, *, refuse_post=False):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == "/v1/event-logs"
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            body = json.dumps(workspace.list_logs(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            posts.append(self.path)
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            assert self.headers["x-owg-local-storage-confirmed"] == "true"
            url = parse.urlsplit(self.path)
            log_id = url.path.split("/")[3]
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if refuse_post:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"refused"}')
                return
            result = workspace.append_log(
                log_id, BytesIO(body), source_format="csv", mapping=mapping,
            )
            encoded = json.dumps(result, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, posts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_single_command_source_journal_and_real_local_append_replay(
        environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port=port), provider_opener=provider) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["status"] == "completed"
        assert first["new_snapshots"] == 1
        assert first["added_events"] == 1
        assert first["source_writes_performed"] is False
        assert first["historical_completeness"] == "unknown"
        assert (environment[0] / "recovery-1.csv").exists()
        assert main(cli(environment, port=port, output="recovery-2.csv"),
                    provider_opener=provider) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["replayed_snapshots"] == 1
        assert second["added_events"] == 0
        assert second["duplicate_events_skipped"] == 1
        assert len(posts) == 2
    assert provider.calls == 2
    assert workspace.summary(environment[2])["events"] == 2
    assert TOKEN not in json.dumps(first) + json.dumps(second)
    assert SESSION not in json.dumps(first) + json.dumps(second)
    assert INCIDENT not in json.dumps(first) + json.dumps(second)


def test_new_provider_revision_appends_one_new_event(environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port=port), provider_opener=provider) == 0
        capsys.readouterr()
        provider.revision = 4
        assert main(cli(environment, port=port, output="second.csv"),
                    provider_opener=provider) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["new_snapshots"] == 1
        assert report["added_events"] == 1
        assert report["duplicate_events_skipped"] == 1
        assert len(posts) == 2
    assert workspace.summary(environment[2])["events"] == 3


@pytest.mark.parametrize("mode", ["no_consent", "revoked", "wrong_tenant",
                                  "wrong_target", "existing_output"])
def test_preflight_refuses_before_provider_read(environment, capsys, mode):
    root, grant, _, mapping = environment
    provider = Provider()
    if mode == "revoked":
        data = json.loads(grant.read_text())
        data["enabled"] = False
        grant.write_text(json.dumps(data))
    if mode == "existing_output":
        (root / "recovery-1.csv").write_text("do not overwrite")
    with owg_server(mapping) as (port, posts):
        args = cli(environment, port=port, consent=mode != "no_consent",
                   tenant="team-b" if mode == "wrong_tenant" else "team-a",
                   log_id="log_" + "0" * 16 if mode == "wrong_target" else None)
        assert main(args, provider_opener=provider) == 2
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "refused"
        assert report["possible_local_append"] is False
        assert not posts
    assert provider.calls == 0
    assert not (root / "journal.sqlite").exists()


def test_provider_refusal_cannot_append(environment, capsys):
    provider = Provider(status=403)
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port=port), provider_opener=provider) == 2
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "refused"
        assert not posts
    assert provider.calls == 1


def test_ambiguous_local_post_keeps_recoverable_journal_and_does_not_retry(
        environment, capsys):
    provider = Provider()
    with owg_server(environment[3], refuse_post=True) as (port, posts):
        assert main(cli(environment, port=port), provider_opener=provider) == 3
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "append_unverified"
        assert report["possible_local_append"] is True
        assert report["journal_committed"] is True
        assert (environment[0] / "journal.sqlite").exists()
        assert (environment[0] / "recovery-1.csv").exists()
        assert len(posts) == 1
    assert provider.calls == 1
