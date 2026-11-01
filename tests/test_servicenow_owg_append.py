"""Synthetic ServiceNow journal -> real local HTTP -> real OWG append acceptance.

No provider or customer tenant is contacted. The HTTP server is loopback-only
and exercises the existing event-log engine with a disposable local database.
"""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from threading import Thread
from urllib import error, parse

import pytest

from server import event_log_workspace as workspace
from server.servicenow_incident_journal import (
    _export_csv, _scope_digest, update_journal,
)
from server.servicenow_incident_staging import SelectedIncidents
from server.servicenow_owg_append import main

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
SESSION = "FAKE-LOCAL-SESSION-NOT-A-CREDENTIAL"
LOG_ID_PATTERN = "log_"


def row(suffix, activity, timestamp):
    case = "snowcase:" + hashlib.sha256(b"selected-case").hexdigest()
    event = "snowevent:" + hashlib.sha256(suffix.encode()).hexdigest()
    return (case, "Incident state: " + activity, timestamp, event)


BASE = row("old", "1", "2026-10-30T01:00:00+00:00")
NEW = row("new", "2", "2026-10-30T02:00:00+00:00")


@pytest.fixture
def setup_journal(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    grant = tmp_path / "grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": "team-a", "sys_ids": [INCIDENT],
    }), encoding="utf-8")
    if os.name == "posix":
        grant.chmod(0o600)
    scope = SelectedIncidents(
        host=HOST, tenant_scope="team-a", sys_ids=(INCIDENT,),
        consented=True, grant_current=True,
    )
    journal = tmp_path / "snapshots.sqlite"
    rows, added, _ = update_journal(
        scope, timezone_name="UTC", journal_file=journal,
        grant_file=grant, incoming=[NEW],
    )
    assert len(rows) == added == 1
    namespace = "snow_" + _scope_digest(scope, "UTC")[:32]
    mapping = {
        "case_column": "case_id", "activity_column": "activity",
        "timestamp_column": "timestamp", "event_id_column": "source_event_id",
        "source_namespace": namespace, "case_namespace": namespace,
    }
    made = workspace.import_log(
        BytesIO(_export_csv([BASE]).encode()), name="Local ServiceNow test",
        source_format="csv", mapping=mapping,
    )
    return tmp_path, grant, journal, made["log_id"], mapping


def args(data, *, consent=True, confirm=True, tenant="team-a", port=8765):
    root, grant, journal, log_id, _ = data
    cli = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(journal), "--log-id", log_id,
        "--confirm-log-id", log_id if confirm else "log_" + "0" * 16,
        "--port", str(port),
    ]
    if consent:
        cli += ["--consent-local-journal-read", "--consent-local-append"]
    return cli


@contextmanager
def local_owg_server(mapping, *, refuse=False):
    """Real HTTP transport; the handler invokes the real OWG event-log engine."""
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.path)
            body = self.rfile.read(int(self.headers["Content-Length"]))
            url = parse.urlsplit(self.path)
            params = parse.parse_qs(url.query)
            log_id = url.path.split("/")[3]
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            assert self.headers["x-owg-local-storage-confirmed"] == "true"
            assert self.headers["Content-Type"].startswith("text/csv")
            assert params == {
                "format": ["csv"], "case_column": ["case_id"],
                "activity_column": ["activity"], "timestamp_column": ["timestamp"],
                "event_id_column": ["source_event_id"],
                "source_namespace": [mapping["source_namespace"]],
                "case_namespace": [mapping["case_namespace"]],
            }
            if refuse:
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail":"forbidden"}')
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
        yield server.server_port, calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_real_loopback_http_append_and_idempotent_retry(
        setup_journal, monkeypatch, capsys):
    data = setup_journal
    monkeypatch.setenv("HTTP_PROXY", "http://not-a-real-proxy.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://not-a-real-proxy.invalid:9999")
    with local_owg_server(data[4]) as (port, calls):
        assert main(args(data, port=port)) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["status"] == "completed"
        assert first["added_events"] == 1
        assert first["rejected_events"] == 0
        assert first["journal_snapshots"] == 1
        assert first["provider_read_performed"] is False
        assert first["source_writes_performed"] is False
        assert main(args(data, port=port)) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["added_events"] == 0
        assert second["duplicate_events_skipped"] == 1
        assert len(calls) == 2
    assert workspace.summary(data[3])["events"] == 2
    assert len(workspace.append_history(data[3])["appends"]) == 2
    assert SESSION not in json.dumps(first) + json.dumps(second)
    assert INCIDENT not in json.dumps(first) + json.dumps(second)


@pytest.mark.parametrize("change", ["no_consent", "wrong_target", "wrong_tenant",
                                    "revoked", "missing_session", "bad_port"])
def test_preflight_refuses_before_any_http(
        setup_journal, monkeypatch, capsys, change):
    data = setup_journal
    cli = args(data)
    if change == "no_consent":
        cli = args(data, consent=False)
    elif change == "wrong_target":
        cli = args(data, confirm=False)
    elif change == "wrong_tenant":
        cli = args(data, tenant="team-b")
    elif change == "revoked":
        grant = data[1]
        value = json.loads(grant.read_text())
        value["enabled"] = False
        grant.write_text(json.dumps(value))
    elif change == "missing_session":
        monkeypatch.delenv("OWG_DASHBOARD_SESSION")
    elif change == "bad_port":
        cli = args(data, port=65536)

    class NeverCalled:
        def open(self, *_args, **_kwargs):
            pytest.fail("No HTTP request permitted after failed preflight")

    assert main(cli, opener=NeverCalled()) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "refused"
    assert report["possible_local_append"] is False
    assert SESSION not in json.dumps(report)


def test_local_http_rejection_is_ambiguous_until_history_checked(
        setup_journal, capsys):
    data = setup_journal
    with local_owg_server(data[4], refuse=True) as (port, calls):
        assert main(args(data, port=port)) == 3
        assert len(calls) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unverified"
    assert report["possible_local_append"] is True
    assert workspace.summary(data[3])["events"] == 1


def test_transport_failure_is_unverified_and_does_not_echo_credentials(
        setup_journal, capsys):
    class Fails:
        def open(self, *_args, **_kwargs):
            raise error.URLError("PRIVATE-ERROR-CONTAINING-TOKEN")

    assert main(args(setup_journal), opener=Fails()) == 3
    output = capsys.readouterr().out
    assert json.loads(output)["status"] == "unverified"
    assert "PRIVATE-" not in output
    assert SESSION not in output


def test_corrupted_journal_row_refused_before_http(setup_journal, capsys):
    data = setup_journal
    with sqlite3.connect(data[2]) as db:
        db.execute("UPDATE events SET timestamp='not-a-timestamp'")

    class NeverCalled:
        def open(self, *_args, **_kwargs):
            pytest.fail("Invalid journal must never reach HTTP")

    assert main(args(data), opener=NeverCalled()) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_fastapi_route_requires_real_local_session_and_storage_confirmation(
        setup_journal, monkeypatch):
    """Exercise the actual API contract, not only the synthetic HTTP handler."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import event_log_routes

    data = setup_journal
    monkeypatch.setattr(event_log_routes, "dashboard_session_valid",
                        lambda value: value == SESSION)
    app = FastAPI()
    app.include_router(event_log_routes.router)
    namespace = data[4]["source_namespace"]
    query = parse.urlencode({
        "format": "csv", "case_column": "case_id",
        "activity_column": "activity", "timestamp_column": "timestamp",
        "event_id_column": "source_event_id", "source_namespace": namespace,
        "case_namespace": namespace,
    })
    url = f"/v1/event-logs/{data[3]}/append?{query}"
    body = _export_csv([NEW]).encode()
    with TestClient(app) as client:
        forbidden = client.post(url, content=body, headers={
            "Authorization": "OWG-Session " + SESSION,
        })
        assert forbidden.status_code == 403
        forbidden = client.post(url, content=body, headers={
            "Authorization": "Bearer " + SESSION,
            "x-owg-local-storage-confirmed": "true",
        })
        assert forbidden.status_code == 403
        good = client.post(url, content=body, headers={
            "Authorization": "OWG-Session " + SESSION,
            "x-owg-local-storage-confirmed": "true",
        })
        assert good.status_code == 200, good.text
        assert good.json()["added_events"] == 1
        again = client.post(url, content=body, headers={
            "Authorization": "OWG-Session " + SESSION,
            "x-owg-local-storage-confirmed": "true",
        })
        assert again.status_code == 200
        assert again.json()["duplicate_events_skipped"] == 1
