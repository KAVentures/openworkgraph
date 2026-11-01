"""Synthetic ServiceNow plus disposable actual local OWG HTTP checkpoint acceptance.

No live provider, customer data, non-loopback HTTP or source writes.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import hashlib
import json
import os
from threading import Thread
from urllib import parse

import pytest

from server import event_log_workspace as workspace
from server.servicenow_incident_checkpoint import main
from server.servicenow_incident_journal import _export_csv, _scope_digest
from server.servicenow_incident_staging import SelectedIncidents

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
TOKEN = "FAKE-PROVIDER-TOKEN-NOT-REAL"
SESSION = "FAKE-LOCAL-SESSION-NOT-REAL"
NOW = datetime(2026, 11, 1, 22, 0, tzinfo=timezone.utc)


class ProviderResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, url, records):
        self.url, self.records = url, records

    def geturl(self):
        return self.url

    def read(self, size):
        return json.dumps({"result": self.records}).encode()[:size]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Provider:
    def __init__(self):
        self.calls = 0
        self.since_values = []
        self.updated = "2026-10-30 03:10:00"
        self.revision = 3

    def open(self, req, timeout):
        assert req.get_method() == "GET" and timeout == 15
        assert req.full_url.startswith("https://" + HOST + "/api/now/table/incident?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        query = parse.parse_qs(parse.urlsplit(req.full_url).query)["sysparm_query"][0]
        since = query.split("sys_updated_on>=", 1)[1].split("^", 1)[0]
        self.since_values.append(since)
        self.calls += 1
        records = []
        if self.updated >= since:
            records = [{"sys_id": INCIDENT, "sys_mod_count": str(self.revision),
                        "sys_updated_on": self.updated, "state": str(self.revision),
                        "short_description": "PRIVATE-PROVIDER-TEXT"}]
        return ProviderResponse(req.full_url, records)


@pytest.fixture
def environment(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({"version": 1, "enabled": True, "consented": True,
                                 "host": HOST, "tenant_scope": "team-a",
                                 "sys_ids": [INCIDENT]}))
    if os.name == "posix":
        grant.chmod(0o600)
    scope = SelectedIncidents(host=HOST, tenant_scope="team-a", sys_ids=(INCIDENT,),
                              consented=True, grant_current=True)
    namespace = "snow_" + _scope_digest(scope, "UTC")[:32]
    mapping = {"case_column": "case_id", "activity_column": "activity",
               "timestamp_column": "timestamp", "event_id_column": "source_event_id",
               "source_namespace": namespace, "case_namespace": namespace}
    baseline = ("snowcase:" + hashlib.sha256(b"baseline").hexdigest(),
                "Incident state: baseline", "2026-10-20T00:00:00+00:00",
                "snowevent:" + hashlib.sha256(b"baseline").hexdigest())
    log = workspace.import_log(BytesIO(_export_csv([baseline]).encode()),
                               name="Selected ServiceNow", source_format="csv",
                               mapping=mapping)
    return tmp_path, grant, log["log_id"], mapping


@contextmanager
def owg_server(mapping, *, refuse_post=False):
    posts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            assert self.path == "/v1/event-logs"
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            payload = json.dumps(workspace.list_logs(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            posts.append(self.path)
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            assert self.headers["x-owg-local-storage-confirmed"] == "true"
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if refuse_post:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"refused"}')
                return
            log_id = parse.urlsplit(self.path).path.split("/")[3]
            result = workspace.append_log(log_id, BytesIO(body),
                                          source_format="csv", mapping=mapping)
            payload = json.dumps(result, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, posts
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def cli(env, port, *, output="one.csv", first=False, tenant="team-a", consent=True):
    root, grant, log, _ = env
    args = ["--instance", HOST, "--tenant", tenant,
            "--incident-id", INCIDENT, "--grant-file", str(grant),
            "--timezone", "UTC", "--journal", str(root / "journal.sqlite"),
            "--checkpoint", str(root / "checkpoint.json"),
            "--output", str(root / output), "--log-id", log,
            "--confirm-log-id", log, "--port", str(port)]
    if first:
        args += ["--initial-since", "2026-10-20 00:00:00"]
    if consent:
        args += ["--consent-selected-read", "--consent-local-journal-read",
                 "--consent-local-append", "--consent-incremental-checkpoint"]
    return args


def test_incremental_read_journal_real_local_append_and_durable_replay(environment, capsys):
    provider = Provider()
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port, first=True), provider_opener=provider,
                    now_fn=lambda: NOW) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["checkpoint_advanced"] is True
        assert first["added_events"] == 1
        assert provider.since_values == ["2026-10-20 00:00:00"]
        provider.updated = "2026-10-31 04:10:00"
        provider.revision = 4
        assert main(cli(environment, port, output="two.csv"),
                    provider_opener=provider, now_fn=lambda: NOW) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["added_events"] == 1
        assert provider.since_values[1] == "2026-10-28 03:10:00"
        assert len(posts) == 2
        assert main(cli(environment, port, output="three.csv"),
                    provider_opener=provider, now_fn=lambda: NOW) == 0
        third = json.loads(capsys.readouterr().out)
        assert third["added_events"] == 0
        assert third["checkpoint_advanced"] is False
        assert provider.since_values[2] == "2026-10-29 04:10:00"
    assert workspace.summary(environment[2])["events"] == 3
    state = json.loads((environment[0] / "checkpoint.json").read_text())
    assert state["latest_source_utc"] == "2026-10-31T04:10:00+00:00"
    assert INCIDENT not in json.dumps(state) + json.dumps(first) + json.dumps(second)
    assert TOKEN not in json.dumps(state) + json.dumps(first) + json.dumps(second)
    assert SESSION not in json.dumps(state) + json.dumps(first) + json.dumps(second)


@pytest.mark.parametrize("mode", ["missing_consent", "wrong_tenant", "stale_lock",
                                   "missing_initial", "bad_initial", "wrong_checkpoint_binding"])
def test_refuse_before_provider_read(environment, capsys, mode):
    provider = Provider()
    root, grant, log, _ = environment
    args = cli(environment, 8765, first=True)
    if mode == "missing_consent":
        args.remove("--consent-incremental-checkpoint")
    elif mode == "wrong_tenant":
        args[args.index("--tenant") + 1] = "team-b"
    elif mode == "stale_lock":
        (root / "checkpoint.json.lock").write_text("busy")
    elif mode == "missing_initial":
        args = cli(environment, 8765)
    elif mode == "bad_initial":
        args[args.index("--initial-since") + 1] = "2026-03-29 02:30:00"
        args[args.index("--timezone") + 1] = "Europe/Stockholm"
    elif mode == "wrong_checkpoint_binding":
        (root / "checkpoint.json").write_text(json.dumps({
            "format": "openworkgraph.servicenow-incremental-checkpoint.v1",
            "binding": "0" * 64, "initial_since": "2026-10-20 00:00:00",
            "latest_source_utc": None}))
        if os.name == "posix":
            (root / "checkpoint.json").chmod(0o600)
    assert main(args, provider_opener=provider, now_fn=lambda: NOW) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert provider.calls == 0
    assert not (root / "journal.sqlite").exists()
    assert not (root / "checkpoint.json").exists() or mode == "wrong_checkpoint_binding"


def test_uncertain_local_append_does_not_commit_checkpoint(environment, capsys):
    provider = Provider()
    with owg_server(environment[3], refuse_post=True) as (port, posts):
        assert main(cli(environment, port, first=True), provider_opener=provider,
                    now_fn=lambda: NOW) == 3
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "append_unverified"
        assert result["checkpoint_committed"] is False
        assert len(posts) == 1
    assert not (environment[0] / "checkpoint.json").exists()
    assert provider.calls == 1


def test_post_append_checkpoint_failure_reports_unverified(environment, capsys, monkeypatch):
    import server.servicenow_incident_checkpoint as module
    provider = Provider()

    def refuse(*_args, **_kwargs):
        raise module.CheckpointError("test failure")

    monkeypatch.setattr(module, "_publish_state", refuse)
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port, first=True), provider_opener=provider,
                    now_fn=lambda: NOW) == 3
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "append_completed_checkpoint_unverified"
        assert len(posts) == 1
    assert not (environment[0] / "checkpoint.json").exists()
    assert workspace.summary(environment[2])["events"] == 2


@pytest.mark.parametrize("latest, expected", [
    ("2026-03-31T12:00:00+00:00", "2026-03-29 14:00:00"),
    ("2026-10-27T12:00:00+00:00", "2026-10-25 13:00:00"),
])
def test_overlap_uses_utc_subtraction_then_declared_local_dst(latest, expected):
    from zoneinfo import ZoneInfo
    from server.servicenow_incident_checkpoint import _since
    state = {"initial_since": "2026-03-01 00:00:00",
             "latest_source_utc": latest}
    assert _since(state, ZoneInfo("Europe/Stockholm")) == expected
    state["initial_since"] = "2026-11-01 00:00:00"
    assert _since(state, ZoneInfo("Europe/Stockholm")) == state["initial_since"]


def test_missing_required_child_counts_never_commits_checkpoint(environment, capsys, monkeypatch):
    import server.servicenow_incident_checkpoint as module
    provider = Provider()

    def incomplete_receipt(_args, **_kwargs):
        print(json.dumps({"format": "openworkgraph.servicenow-selected-run.v1",
                          "status": "completed", "local_owg_append_performed": True,
                          "source_writes_performed": False, "checkpoint_advanced": False,
                          "historical_completeness": "unknown", "rejected_events": 0}))
        return 0

    monkeypatch.setattr(module, "run_main", incomplete_receipt)
    assert main(cli(environment, 8765, first=True), provider_opener=provider,
                now_fn=lambda: NOW) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "append_unverified"
    assert not (environment[0] / "checkpoint.json").exists()
    assert provider.calls == 0


def test_revoked_grant_refuses_before_source_get(environment, capsys):
    provider = Provider()
    root, grant, _, _ = environment
    document = json.loads(grant.read_text())
    document["enabled"] = False
    grant.write_text(json.dumps(document))
    assert main(cli(environment, 8765, first=True), provider_opener=provider,
                now_fn=lambda: NOW) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert provider.calls == 0
    assert not (root / "checkpoint.json").exists()


def test_revocation_after_append_does_not_advance_checkpoint(environment, capsys, monkeypatch):
    import server.servicenow_incident_checkpoint as module
    provider = Provider()
    root, grant, _, _ = environment
    actual = module._journal_latest

    def revoke_after_append(*args):
        document = json.loads(grant.read_text())
        document["enabled"] = False
        grant.write_text(json.dumps(document))
        return actual(*args)

    monkeypatch.setattr(module, "_journal_latest", revoke_after_append)
    with owg_server(environment[3]) as (port, posts):
        assert main(cli(environment, port, first=True), provider_opener=provider,
                    now_fn=lambda: NOW) == 3
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "append_completed_checkpoint_unverified"
        assert len(posts) == 1
    assert workspace.summary(environment[2])["events"] == 2
    assert not (root / "checkpoint.json").exists()
