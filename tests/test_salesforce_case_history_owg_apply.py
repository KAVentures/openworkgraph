"""Salesforce reviewed extraction -> actual OWG FastAPI TCP append -> checkpoint."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
import json
import os
import socket
from threading import Thread
import time
from urllib import parse

import pytest

from server.salesforce_case_history_incremental import prepare
from server.salesforce_case_history_owg_apply import ApplyError, UncertainApply, apply, main

CASE = "500000000000001"
TOKEN = "salesforce-local-test-token-only"
SESSION = "local-dashboard-session-token-for-tests"
START = "2026-10-01T00:00:00Z"
END = "2026-10-03T00:00:00Z"
ROW = {"Id": "017000000000001", "CaseId": CASE,
       "CreatedDate": "2026-10-02T12:00:00Z", "Field": "Status",
       "NewValue": "In Progress"}


@contextmanager
def provider():
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = parse.urlsplit(self.path)
            q = parse.parse_qs(parsed.query).get("q", [""])[0]
            calls.append((self.command, self.headers.get("Authorization")))
            if (parsed.path != "/services/data/v60.0/query"
                    or self.headers.get("Authorization") != "Bearer " + TOKEN
                    or "FROM CaseHistory" not in q):
                self.send_response(401)
                self.end_headers()
                return
            body = json.dumps({"done": True, "totalSize": 1, "records": [ROW]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port), calls
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.fixture
def context(tmp_path, monkeypatch):
    import duckdb  # Required native integration dependency: never silently skip.
    import uvicorn
    from fastapi import FastAPI
    from server import event_log_routes as routes
    from server import event_log_workspace as workspace

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    if os.name == "posix":
        private.chmod(0o700)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "DATA_DIR", tmp_path)
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda value: value == SESSION)

    app = FastAPI()
    app.include_router(routes.router)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                           log_level="error", lifespan="off"))
    thread = Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(.025)
    assert server.started
    try:
        yield private, port, workspace
    finally:
        server.should_exit = True
        thread.join(timeout=8)
        sock.close()
        assert not thread.is_alive()


def reviewed(private):
    with provider() as (origin, calls):
        receipt = prepare(instance=origin, confirm_host="127.0.0.1",
                          namespace="approved-sandbox", cases=[CASE],
                          bootstrap_since=START, until=END, overlap_hours=24,
                          token=TOKEN, checkpoint=private / "checkpoint.json",
                          manifest=private / "review.json",
                          output=private / "history.csv", allow_loopback_http=True)
    assert len(calls) == 1 and calls[0] == ("GET", "Bearer " + TOKEN)
    return receipt


def initial_log(workspace, receipt, *, namespace_override=None):
    ns = namespace_override or "sf_" + receipt["scope_sha256"][:32]
    # A distinct seed key makes the next apply add one new event.
    csv = ("case_id,activity,timestamp,source_event_id\n"
           + "sf:" + "a" * 64 + ",Case created,2026-10-01T01:00:00+00:00,sf:"
           + "b" * 64 + "\n").encode()
    made = workspace.import_log(BytesIO(csv), name="Reviewed Salesforce source",
                                source_format="csv", mapping={
                                    "case_column": "case_id", "activity_column": "activity",
                                    "timestamp_column": "timestamp",
                                    "event_id_column": "source_event_id",
                                    "source_namespace": ns, "case_namespace": ns})
    return made["log_id"]


def approved(private, port, log_id, receipt):
    return dict(checkpoint=private / "checkpoint.json", manifest=private / "review.json",
                confirm_manifest_sha256=receipt["manifest_sha256"],
                log_id=log_id, confirm_log_id=log_id, port=port,
                consent_prepared_read=True, consent_local_append=True,
                consent_checkpoint_advance=True, session=SESSION)


def test_actual_source_tcp_to_actual_owg_rest_append_and_checkpoint(context):
    private, port, workspace = context
    receipt = reviewed(private)
    assert not (private / "checkpoint.json").exists()
    log_id = initial_log(workspace, receipt)
    result = apply(**approved(private, port, log_id, receipt))
    assert result["status"] == "applied" and result["added_events"] == 1
    assert result["duplicate_events_skipped"] == 0
    assert result["owg_append_readback_verified"] is True
    assert result["checkpoint_advanced"] is True
    assert workspace.list_logs()["logs"][0]["events"] == 2
    assert workspace.append_history(log_id)["appends"][-1]["added_events"] == 1
    assert json.loads((private / "checkpoint.json").read_text())["until"] == END


def test_unknown_owg_outcome_never_advances_checkpoint_and_retry_deduplicates(context, monkeypatch):
    private, port, workspace = context
    receipt = reviewed(private)
    log_id = initial_log(workspace, receipt)
    import server.salesforce_case_history_owg_apply as module
    original = module._get_append_history
    monkeypatch.setattr(module, "_get_append_history", lambda *a, **kw:
                        (_ for _ in ()).throw(ApplyError("unavailable")))
    with pytest.raises(UncertainApply):
        apply(**approved(private, port, log_id, receipt))
    assert not (private / "checkpoint.json").exists()
    assert workspace.list_logs()["logs"][0]["events"] == 2
    monkeypatch.setattr(module, "_get_append_history", original)
    result = apply(**approved(private, port, log_id, receipt))
    assert result["added_events"] == 0 and result["duplicate_events_skipped"] == 1
    assert result["checkpoint_advanced"] is True
    assert workspace.list_logs()["logs"][0]["events"] == 2


def test_consent_scope_revocation_and_tampering_refuse_before_post(context, monkeypatch):
    private, port, workspace = context
    receipt = reviewed(private)
    log_id = initial_log(workspace, receipt)
    args = approved(private, port, log_id, receipt)
    with pytest.raises(ApplyError):
        apply(**{**args, "consent_local_append": False})
    with pytest.raises(ApplyError):
        apply(**{**args, "confirm_log_id": "log_" + "0" * 16})
    other = initial_log(workspace, receipt, namespace_override="wrong")
    with pytest.raises(ApplyError, match="different source scope"):
        apply(**{**args, "log_id": other, "confirm_log_id": other})
    from server import event_log_routes as routes
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda value: False)
    with pytest.raises(ApplyError):
        apply(**args)
    assert not workspace.append_history(log_id)["appends"]
    assert not (private / "checkpoint.json").exists()
    (private / "history.csv").write_text((private / "history.csv").read_text() + "\n")
    with pytest.raises(ApplyError, match="CSV changed"):
        apply(**args)


def test_cli_refuses_missing_consents_without_local_write(context, monkeypatch, capsys):
    private, port, workspace = context
    receipt = reviewed(private)
    log_id = initial_log(workspace, receipt)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    flags = ["--checkpoint", str(private / "checkpoint.json"), "--manifest", str(private / "review.json"),
             "--confirm-manifest-sha256", receipt["manifest_sha256"],
             "--log-id", log_id, "--confirm-log-id", log_id, "--port", str(port)]
    assert main(flags) == 2
    out = capsys.readouterr().out
    assert CASE not in out and SESSION not in out and TOKEN not in out
    assert workspace.append_history(log_id)["appends"] == []
    assert not (private / "checkpoint.json").exists()
