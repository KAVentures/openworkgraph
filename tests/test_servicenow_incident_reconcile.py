"""Offline checkpoint reconciliation against a disposable HTTP OWG event-log engine.

Synthetic records and sessions only; no real provider or business-system calls.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
import hashlib
import json
import os
from threading import Thread
from urllib import parse

import pytest

from server import event_log_workspace as workspace
from server.servicenow_incident_journal import _export_csv, _scope_digest, update_journal
from server.servicenow_incident_reconcile import MAX_EXPORT_BYTES, main
from server.servicenow_incident_staging import SelectedIncidents

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
SESSION = "SYNTHETIC-LOCAL-SESSION-NOT-REAL"
NOW = datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc)


def _row(label, stamp, state):
    return (
        "snowcase:" + hashlib.sha256(b"case").hexdigest(),
        "Incident state: " + state, stamp,
        "snowevent:" + hashlib.sha256(label.encode()).hexdigest(),
    )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
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
    baseline = _row("baseline", "2026-10-20T00:00:00+00:00", "baseline")
    pending = _row("pending", "2026-10-31T04:10:00+00:00", "3")
    log = workspace.import_log(BytesIO(_export_csv([baseline]).encode()),
                               name="Synthetic selected incidents", source_format="csv",
                               mapping=mapping)
    journal = tmp_path / "journal.sqlite"
    update_journal(scope, timezone_name="UTC", journal_file=journal,
                   grant_file=grant, incoming=[pending])
    return {"root": tmp_path, "scope": scope, "grant": grant,
            "journal": journal, "log_id": log["log_id"], "mapping": mapping,
            "baseline": baseline, "pending": pending}


@contextmanager
def local_server(*, oversized=False, corrupt=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            requests.append(("POST", self.path))
            self.send_error(405)

        def do_GET(self):
            requests.append(("GET", self.path))
            assert self.headers["Authorization"] == "OWG-Session " + SESSION
            if self.path == "/v1/event-logs":
                payload = json.dumps(workspace.list_logs(), default=str).encode()
                media = "application/json"
            else:
                parts = parse.urlsplit(self.path)
                assert parts.path.endswith("/export")
                assert parse.parse_qs(parts.query) == {
                    "format": ["sourcecsv"], "case_type": ["source_case"]}
                log_id = parts.path.split("/")[3]
                output = StringIO(newline="")
                workspace.export_log(log_id, "sourcecsv", output, "source_case")
                payload = output.getvalue().encode()
                if oversized:
                    payload = b"x" * (MAX_EXPORT_BYTES + 1)
                if corrupt:
                    payload = payload.replace(b"Incident state: 3", b"Incident state: 8")
                media = "text/csv"
            self.send_response(200)
            self.send_header("Content-Type", media)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def cli(env, port, *, first=True):
    args = ["--instance", HOST, "--tenant", "team-a",
            "--incident-id", INCIDENT, "--grant-file", str(env["grant"]),
            "--timezone", "UTC", "--journal", str(env["journal"]),
            "--checkpoint", str(env["root"] / "checkpoint.json"),
            "--log-id", env["log_id"], "--confirm-log-id", env["log_id"],
            "--port", str(port), "--consent-local-journal-read",
            "--consent-local-log-read", "--consent-checkpoint-recovery"]
    if first:
        args += ["--initial-since", "2026-10-20 00:00:00"]
    return args


def _append(env, *, row=None):
    source = env["pending"] if row is None else row
    workspace.append_log(env["log_id"], BytesIO(_export_csv([source]).encode()),
                         source_format="csv", mapping=env["mapping"])


def test_recovers_lost_ack_from_real_local_source_export_without_post(environment, capsys):
    _append(environment)  # Append succeeded; checkpoint acknowledgement was lost.
    with local_server() as (port, requests):
        assert main(cli(environment, port), now_fn=lambda: NOW) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "reconciled"
        assert report["verified_journal_snapshots"] == 1
        assert report["checkpoint_advanced"] is True
        assert report["provider_read_performed"] is False
        assert report["local_owg_append_performed"] is False
        assert all(method == "GET" for method, _ in requests)
        assert sum("sourcecsv" in path for _, path in requests) == 1
        assert main(cli(environment, port, first=False), now_fn=lambda: NOW) == 0
        repeated = json.loads(capsys.readouterr().out)
        assert repeated["checkpoint_advanced"] is False
    checkpoint = json.loads((environment["root"] / "checkpoint.json").read_text())
    assert checkpoint["latest_source_utc"] == "2026-10-31T04:10:00+00:00"
    combined = json.dumps(report) + json.dumps(repeated) + json.dumps(checkpoint)
    assert INCIDENT not in combined and SESSION not in combined
    assert environment["pending"][3] not in combined
    assert workspace.summary(environment["log_id"])["events"] == 2


@pytest.mark.parametrize("mode", ["missing", "conflicting", "oversized"])
def test_no_checkpoint_for_missing_conflicting_or_oversized_source_export(
        environment, capsys, mode):
    if mode == "conflicting":
        _append(environment)
    with local_server(oversized=mode == "oversized", corrupt=mode == "conflicting") as (port, requests):
        assert main(cli(environment, port), now_fn=lambda: NOW) == 2
        report = json.loads(capsys.readouterr().out)
        assert report["status"] == "not_reconciled"
        assert all(method == "GET" for method, _ in requests)
    assert not (environment["root"] / "checkpoint.json").exists()


@pytest.mark.parametrize("mode", ["no_consent", "wrong_tenant", "wrong_log",
                                   "revoked", "locked", "no_initial"])
def test_preflight_refuses_before_any_local_http(environment, capsys, mode):
    args = cli(environment, 8765)
    if mode == "no_consent":
        args.remove("--consent-local-log-read")
    elif mode == "wrong_tenant":
        args[args.index("--tenant") + 1] = "team-b"
    elif mode == "wrong_log":
        args[args.index("--confirm-log-id") + 1] = "log_" + "0" * 16
    elif mode == "revoked":
        grant = json.loads(environment["grant"].read_text())
        grant["enabled"] = False
        environment["grant"].write_text(json.dumps(grant))
    elif mode == "locked":
        (environment["root"] / "checkpoint.json.lock").write_text("busy")
    elif mode == "no_initial":
        args = cli(environment, 8765, first=False)
    assert main(args, now_fn=lambda: NOW) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "not_reconciled"
    assert not (environment["root"] / "checkpoint.json").exists()


def test_checkpoint_publication_failure_is_reported_as_uncertain(environment, capsys, monkeypatch):
    import server.servicenow_incident_reconcile as module
    _append(environment)
    def refuse(*_args):
        raise module.CheckpointError("synthetic failure")
    monkeypatch.setattr(module, "_publish_state", refuse)
    with local_server() as (port, requests):
        assert main(cli(environment, port), now_fn=lambda: NOW) == 3
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "checkpoint_commit_unverified"
        assert all(method == "GET" for method, _ in requests)
    assert not (environment["root"] / "checkpoint.json").exists()


def test_later_unverified_journal_write_cannot_advance_checkpoint(environment, capsys, monkeypatch):
    import server.servicenow_incident_reconcile as module
    _append(environment)
    original = module._read_local_export

    def concurrent_new_snapshot(*args, **kwargs):
        later = _row("later", "2026-11-01T06:10:00+00:00", "4")
        update_journal(environment["scope"], timezone_name="UTC",
                       journal_file=environment["journal"],
                       grant_file=environment["grant"], incoming=[later])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_read_local_export", concurrent_new_snapshot)
    with local_server() as (port, _):
        assert main(cli(environment, port), now_fn=lambda: NOW) == 0
        assert json.loads(capsys.readouterr().out)["checkpoint_advanced"] is True
    state = json.loads((environment["root"] / "checkpoint.json").read_text())
    assert state["latest_source_utc"] == "2026-10-31T04:10:00+00:00"
