"""Synthetic ServiceNow provider plus real disposable local OWG HTTP acceptance.

No live tenant, credential, source writes or non-loopback transport.
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
from server.servicenow_incident_poll_append import main

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
            if refuse_post and (refuse_post is True or len(posts) == refuse_post):
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



def poll_cli(env, *, port, runs=2, interval=60, prefix="poll",
             consent=True, tenant="team-a", log_id=None):
    root, grant, saved_log, _ = env
    target = saved_log if log_id is None else log_id
    args = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(root / "journal.sqlite"),
        "--output-dir", str(root), "--output-prefix", prefix,
        "--runs", str(runs), "--interval-seconds", str(interval),
        "--log-id", target, "--confirm-log-id", target,
        "--port", str(port),
    ]
    if consent:
        args += [
            "--consent-selected-read", "--consent-bounded-poll",
            "--consent-local-journal-read", "--consent-local-append",
        ]
    return args


def test_two_polls_append_distinct_revisions_to_real_local_owg(environment, capsys):
    provider = Provider()
    waits = []

    def advance(seconds):
        waits.append(seconds)
        provider.revision = 4

    with owg_server(environment[3]) as (port, posts):
        assert main(poll_cli(environment, port=port),
                    provider_opener=provider, sleep_fn=advance) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "completed"
        assert report["completed_polls"] == 2
        assert report["new_snapshots"] == 2
        assert report["added_events"] == 2
        assert report["duplicate_events_skipped"] == 1
        assert report["recovery_csv_exports_created"] == 2
        assert report["checkpoint_advanced"] is False
        assert report["historical_completeness"] == "unknown"
        assert len(posts) == 2
    assert provider.calls == 2
    assert waits == [5] * 12
    assert workspace.summary(environment[2])["events"] == 3
    assert (environment[0] / "poll-001.csv").exists()
    assert (environment[0] / "poll-002.csv").exists()
    assert TOKEN not in json.dumps(report)
    assert SESSION not in json.dumps(report)
    assert INCIDENT not in json.dumps(report)


def test_replayed_snapshot_is_deduped_across_polls(environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(poll_cli(environment, port=port),
                    provider_opener=provider, sleep_fn=lambda _: None) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["new_snapshots"] == 1
        assert report["replayed_snapshots"] == 1
        assert report["added_events"] == 1
        assert report["duplicate_events_skipped"] == 1
        assert len(posts) == 2
    assert workspace.summary(environment[2])["events"] == 2


def test_revocation_while_waiting_stops_before_second_get(environment, capsys):
    root, grant, _, _ = environment
    provider = Provider()
    waits = []

    def revoke(seconds):
        waits.append(seconds)
        data = json.loads(grant.read_text())
        data["enabled"] = False
        grant.write_text(json.dumps(data))

    with owg_server(environment[3]) as (port, posts):
        assert main(poll_cli(environment, port=port),
                    provider_opener=provider, sleep_fn=revoke) == 2
        assert len(posts) == 1
    assert provider.calls == 1
    assert waits == [5]
    assert (root / "poll-001.csv").exists()
    assert not (root / "poll-002.csv").exists()
    report = json.loads(capsys.readouterr().out)
    assert report["completed_polls"] == 1
    assert report["possible_local_append"] is False


@pytest.mark.parametrize("mode", [
    "no_consent", "wrong_tenant", "wrong_target",
    "existing_later_output", "too_many_polls", "too_long_window",
])
def test_invalid_preflight_refuses_before_source_get(environment, capsys, mode):
    root, grant, _, mapping = environment
    if mode == "existing_later_output":
        (root / "poll-002.csv").write_text("do not overwrite")
    provider = Provider()
    with owg_server(mapping) as (port, posts):
        args = poll_cli(
            environment, port=port, consent=mode != "no_consent",
            tenant="team-b" if mode == "wrong_tenant" else "team-a",
            log_id="log_" + "0" * 16 if mode == "wrong_target" else None,
            runs=9 if mode == "too_many_polls" else
                 8 if mode == "too_long_window" else 2,
            interval=1800 if mode == "too_long_window" else 60,
        )
        assert main(args, provider_opener=provider) == 2
        assert not posts
    assert provider.calls == 0
    assert not (root / "journal.sqlite").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_second_local_post_unverified_stops_without_retry(environment, capsys):
    provider = Provider()
    with owg_server(environment[3], refuse_post=2) as (port, posts):
        assert main(poll_cli(environment, port=port),
                    provider_opener=provider, sleep_fn=lambda _: None) == 3
        assert len(posts) == 2
    assert provider.calls == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "append_unverified"
    assert report["possible_local_append"] is True
    assert report["completed_polls"] == 1
    assert (environment[0] / "poll-002.csv").exists()
    assert workspace.summary(environment[2])["events"] == 2


def test_second_provider_failure_does_not_retry_or_post(environment, capsys):
    class FailSecond(Provider):
        def open(self, req, timeout):
            response = super().open(req, timeout)
            if self.calls == 2:
                response.status = 403
            return response

    provider = FailSecond()
    with owg_server(environment[3]) as (port, posts):
        assert main(poll_cli(environment, port=port),
                    provider_opener=provider, sleep_fn=lambda _: None) == 2
        assert len(posts) == 1
    assert provider.calls == 2
    report = json.loads(capsys.readouterr().out)
    assert report["completed_polls"] == 1
    assert report["possible_local_append"] is False


def test_repeated_run_requires_new_output_prefix(environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(poll_cli(environment, port=port, runs=1),
                    provider_opener=provider) == 0
        capsys.readouterr()
        assert main(poll_cli(environment, port=port, runs=1),
                    provider_opener=provider) == 2
        assert len(posts) == 1
    assert provider.calls == 1
