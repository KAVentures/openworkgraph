"""Disposable real local OWG HTTP + fake ServiceNow sys_audit acceptance.

No real provider, customer tenant or business write is contacted. The OWG
side uses the actual event-log import/append storage engine.
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
from server.servicenow_incident_audit import (
    AuditError, _window, main, PAGE_SIZE, MAX_PAGES,
)
from server.servicenow_incident_journal import _export_csv, _scope_digest
from server.servicenow_incident_staging import SelectedIncidents

HOST = "sample.service-now.com"
INCIDENT = "a" * 32
OTHER = "b" * 32
TOKEN = "FAKE-PROVIDER-TOKEN-NOT-REAL"
SESSION = "FAKE-LOCAL-SESSION-NOT-REAL"
SINCE = "2026-10-30 00:00:00"
UNTIL = "2026-10-31 00:00:00"


def audit_row(index=1, *, field="state", incident=INCIDENT,
              timestamp="2026-10-30 03:10:00"):
    return {
        "sys_id": f"{index:032x}", "documentkey": incident,
        "fieldname": field, "sys_created_on": timestamp,
        "tablename": "incident",
    }


class ProviderResponse:
    status = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, url, rows, *, status=200, media="application/json",
                 redirect=False):
        self.url, self.rows, self.status = url, rows, status
        self.headers = {"Content-Type": media}
        self.redirect = redirect

    def geturl(self):
        return self.url + "/redirect" if self.redirect else self.url

    def read(self, limit):
        return json.dumps({"result": self.rows}).encode("utf-8")[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Provider:
    def __init__(self, rows=None, *, status=200, media="application/json",
                 redirect=False, after_first=None):
        self.rows = [audit_row()] if rows is None else rows
        self.status, self.media, self.redirect = status, media, redirect
        self.after_first = after_first
        self.calls = 0

    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://" + HOST + "/api/now/table/sys_audit?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert timeout == 15
        query = parse.parse_qs(parse.urlsplit(req.full_url).query)
        assert query["sysparm_fields"] == [
            "sys_id,documentkey,fieldname,sys_created_on,tablename"
        ]
        assert "fieldnameINpriority,state" in query["sysparm_query"][0]
        assert "documentkeyIN" + INCIDENT in query["sysparm_query"][0]
        assert query["sysparm_display_value"] == ["false"]
        offset = int(query["sysparm_offset"][0])
        assert offset % PAGE_SIZE == 0 and 0 <= offset < PAGE_SIZE * MAX_PAGES
        self.calls += 1
        if self.after_first and self.calls == 1:
            self.after_first()
        return ProviderResponse(
            req.full_url, self.rows[offset:offset + PAGE_SIZE],
            status=self.status, media=self.media, redirect=self.redirect,
        )


@pytest.fixture
def environment(tmp_path, monkeypatch):
    if os.name == "posix":
        tmp_path.chmod(0o700)
    monkeypatch.setattr(workspace, "DATA_DIR", tmp_path)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    grant = tmp_path / "incident-grant.json"
    audit_grant = tmp_path / "audit-grant.json"
    grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": "team-a", "sys_ids": [INCIDENT],
    }))
    audit_grant.write_text(json.dumps({
        "version": 1, "enabled": True, "consented": True,
        "host": HOST, "tenant_scope": "team-a", "sys_ids": [INCIDENT],
        "table": "sys_audit", "fields": ["state", "priority"],
        "since": SINCE, "until": UNTIL,
    }))
    if os.name == "posix":
        grant.chmod(0o600)
        audit_grant.chmod(0o600)
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
    baseline = (
        "snowcase:" + hashlib.sha256(b"baseline").hexdigest(),
        "Incident state: baseline", "2026-10-30T00:00:00+00:00",
        "snowevent:" + hashlib.sha256(b"baseline").hexdigest(),
    )
    made = workspace.import_log(
        BytesIO(_export_csv([baseline]).encode()), name="ServiceNow selected",
        source_format="csv", mapping=mapping,
    )
    return tmp_path, grant, audit_grant, made["log_id"], mapping


def cli(env, *, output="audit-1.csv", port=8765, tenant="team-a",
        consent=True, log_id=None, fields=None, since=SINCE, until=UNTIL):
    root, grant, audit_grant, saved_log, _ = env
    target = saved_log if log_id is None else log_id
    args = [
        "--instance", HOST, "--tenant", tenant, "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--audit-grant-file", str(audit_grant),
        "--since", since, "--until", until, "--timezone", "UTC",
        "--output", str(root / output), "--log-id", target,
        "--confirm-log-id", target, "--port", str(port),
    ]
    for field in ["state", "priority"] if fields is None else fields:
        args += ["--field", field]
    if consent:
        args += [
            "--consent-audit-read", "--consent-recovery-export",
            "--consent-local-append",
        ]
    return args


@contextmanager
def owg_server(*, refuse_post=False):
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
                log_id, BytesIO(body), source_format="csv",
                mapping=environment_mapping[0],
            )
            encoded = json.dumps(result, default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    # Each test assigns its own mapping before starting the disposable server.
    environment_mapping = [None]
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, posts, environment_mapping
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_selected_audit_read_real_local_append_and_replay(environment, capsys):
    provider = Provider([audit_row(1), audit_row(2, field="priority")])
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 0
        first = json.loads(capsys.readouterr().out)
        assert first["status"] == "completed"
        assert first["audit_observations"] == 2
        assert first["added_events"] == 2
        assert first["old_new_values_requested"] is False
        assert first["historical_completeness"] == "unknown"
        output = (environment[0] / "audit-1.csv").read_text()
        assert "Incident audit: state" in output
        assert "Incident audit: priority" in output
        assert INCIDENT not in output
        assert TOKEN not in output
        assert main(cli(environment, port=port, output="audit-2.csv"),
                    provider_opener=provider) == 0
        second = json.loads(capsys.readouterr().out)
        assert second["added_events"] == 0
        assert second["duplicate_events_skipped"] == 2
        assert len(posts) == 2
    assert provider.calls == 2
    assert workspace.summary(environment[3])["events"] == 3
    assert all(value not in json.dumps(first) + json.dumps(second)
               for value in (TOKEN, SESSION, INCIDENT))


def test_bounded_multi_page_audit_real_local_append(environment, capsys):
    provider = Provider([audit_row(i + 1) for i in range(101)])
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["audit_observations"] == 101
        assert report["added_events"] == 101
        assert provider.calls == 2
        assert len(posts) == 1
    assert workspace.summary(environment[3])["events"] == 102


@pytest.mark.parametrize("mode", [
    "no_consent", "audit_revoked", "incident_revoked", "wrong_tenant",
    "wrong_log", "existing_output", "wrong_field", "duplicate_field",
    "wrong_audit_window", "wrong_audit_scope",
])
def test_preflight_refuses_before_provider_read(environment, capsys, mode):
    root, grant, audit_grant, _, _ = environment
    provider = Provider()
    kwargs = {}
    if mode == "no_consent":
        kwargs["consent"] = False
    if mode == "audit_revoked":
        data = json.loads(audit_grant.read_text())
        data["enabled"] = False
        audit_grant.write_text(json.dumps(data))
    if mode == "incident_revoked":
        data = json.loads(grant.read_text())
        data["enabled"] = False
        grant.write_text(json.dumps(data))
    if mode == "wrong_tenant":
        kwargs["tenant"] = "team-b"
    if mode == "wrong_log":
        kwargs["log_id"] = "log_" + "f" * 16
    if mode == "existing_output":
        (root / "audit-1.csv").write_text("never overwrite")
    if mode == "wrong_field":
        kwargs["fields"] = ["short_description"]
    if mode == "duplicate_field":
        kwargs["fields"] = ["state", "state"]
    if mode == "wrong_audit_window":
        kwargs["since"] = "2026-10-29 00:00:00"
    if mode == "wrong_audit_scope":
        data = json.loads(audit_grant.read_text())
        data["sys_ids"] = [OTHER]
        audit_grant.write_text(json.dumps(data))
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        code = main(cli(environment, port=port, **kwargs), provider_opener=provider)
    assert code == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert provider.calls == 0
    assert not posts


@pytest.mark.parametrize("mutation", [
    {"documentkey": OTHER}, {"fieldname": "short_description"},
    {"tablename": "task"}, {"sys_id": "NOT_AN_ID"},
    {"sys_created_on": "2026-11-01 00:00:00"},
    {"oldvalue": "PRIVATE"},
])
def test_source_scope_and_minimization_refusals(environment, capsys, mutation):
    row = audit_row()
    row.update(mutation)
    provider = Provider([row])
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not (environment[0] / "audit-1.csv").exists()
    assert not posts


def test_truncated_pagination_refuses_without_partial_append(environment, capsys):
    provider = Provider([audit_row(i + 1) for i in range(PAGE_SIZE * MAX_PAGES)])
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 2
    assert provider.calls == MAX_PAGES
    assert json.loads(capsys.readouterr().out)["status"] == "refused"
    assert not (environment[0] / "audit-1.csv").exists()
    assert not posts


def test_revocation_after_first_provider_page_stops_before_append(
        environment, capsys):
    grant = environment[2]
    def revoke():
        data = json.loads(grant.read_text())
        data["enabled"] = False
        grant.write_text(json.dumps(data))
    provider = Provider([audit_row(i + 1) for i in range(101)],
                        after_first=revoke)
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 2
    assert provider.calls == 1
    assert not posts
    assert not (environment[0] / "audit-1.csv").exists()
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_failed_local_append_is_uncertain_and_recovery_csv_remains(
        environment, capsys):
    provider = Provider()
    with owg_server(refuse_post=True) as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 3
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "append_unverified"
    assert report["possible_local_append"] is True
    assert report["recovery_csv_may_exist"] is True
    assert len(posts) == 1
    assert (environment[0] / "audit-1.csv").exists()


def test_no_source_observations_exports_empty_csv_without_post(environment, capsys):
    provider = Provider([])
    with owg_server() as (port, posts, mapping):
        mapping[0] = environment[4]
        assert main(cli(environment, port=port), provider_opener=provider) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["audit_observations"] == 0
    assert report["local_owg_append_performed"] is False
    assert not posts
    assert (environment[0] / "audit-1.csv").read_text().startswith("case_id,activity")


def test_dst_fold_and_nonexistent_window_refused():
    with pytest.raises(AuditError):
        _window("2026-10-25 02:30:00", "2026-10-25 03:30:00",
                "Europe/Stockholm")
    with pytest.raises(AuditError):
        _window("2026-03-29 02:30:00", "2026-03-29 03:30:00",
                "Europe/Stockholm")
    with pytest.raises(AuditError):
        _window("2026-10-20 00:00:00", "2026-10-31 00:00:00", "UTC")
