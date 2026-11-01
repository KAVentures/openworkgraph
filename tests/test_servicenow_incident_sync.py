"""Fake-provider acceptance for the opt-in one-shot ServiceNow HTTPS/CSV slice.

No real network, ServiceNow tenant, credentials, business writes or live grant.
"""
from __future__ import annotations

import csv
import io
import json
import os
from pathlib import Path
from urllib import request

import pytest

from server.servicenow_incident_staging import SelectedIncidents, StagingError
from server.servicenow_incident_sync import (
    SyncError, _http_opener, _source_local_to_utc, main, project_csv,
    read_selected_once,
)

A = "a" * 32
B = "b" * 32
HOST = "sample.service-now.com"
TOKEN = "synthetic-token-DO-NOT-USE"


def row(incident=A, *, revision="3", state="2", updated="2026-10-30 03:10:00"):
    return {"sys_id": incident, "sys_updated_on": updated,
            "sys_mod_count": revision, "state": state,
            "short_description": "PRIVATE-PROVIDER-TEXT", "caller_id": "PRIVATE-CALLER"}


class FakeResponse:
    def __init__(self, rows, *, status=200, content_type="application/json", final_url=None, body=None):
        self.status = status
        self.headers = {"Content-Type": content_type}
        self.final_url = final_url
        self.body = body if body is not None else json.dumps({"result": rows}).encode()

    def geturl(self):
        return self.final_url

    def read(self, size):
        return self.body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, rows=None, *, status=200, content_type="application/json",
                 final_url=None, body=None, on_open=None):
        self.rows = [row()] if rows is None else rows
        self.status = status
        self.content_type = content_type
        self.final_url = final_url
        self.body = body
        self.on_open = on_open
        self.calls = []

    def open(self, req, timeout):
        assert req.get_method() == "GET"
        assert req.full_url.startswith("https://" + HOST + "/api/now/table/incident?")
        assert req.get_header("Authorization") == "Bearer " + TOKEN
        assert req.get_header("Accept") == "application/json"
        assert timeout == 15
        self.calls.append(req.full_url)
        if self.on_open:
            self.on_open()
        return FakeResponse(self.rows, status=self.status, content_type=self.content_type,
                            final_url=self.final_url or req.full_url, body=self.body)


def grant(tmp_path, *, host=HOST, tenant="team-a", ids=(A,), enabled=True, consented=True):
    path = tmp_path / "selected-grant.json"
    path.write_text(json.dumps({"version": 1, "enabled": enabled, "consented": consented,
                                "host": host, "tenant_scope": tenant, "sys_ids": list(ids)}))
    path.chmod(0o600)
    return path


def scope(*, tenant="team-a", ids=(A,), consent=True):
    return SelectedIncidents(host=HOST, tenant_scope=tenant, sys_ids=ids,
                             consented=consent, grant_current=True)


def cli_args(tmp_path, grant_path, output_name="selected.csv", *, tenant="team-a",
             since=None, tz="UTC", consent=True):
    args = ["--instance", HOST, "--tenant", tenant, "--incident-id", A,
            "--grant-file", str(grant_path), "--timezone", tz,
            "--output", str(tmp_path / output_name)]
    if consent:
        args.append("--consent-selected-read")
    if since:
        args += ["--since", since]
    return args


def test_one_shot_https_read_produces_importable_csv_and_repeatable_source_keys(
        tmp_path, monkeypatch, capsys):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    first = FakeOpener()
    assert main(cli_args(tmp_path, path), opener=first) == 0
    assert len(first.calls) == 1
    output = tmp_path / "selected.csv"
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert set(rows[0]) == {"case_id", "activity", "timestamp", "source_event_id"}
    assert rows[0]["activity"] == "Incident state: 2"
    assert rows[0]["timestamp"] == "2026-10-30T03:10:00+00:00"
    assert rows[0]["source_event_id"].startswith("snowevent:")
    assert A not in output.read_text()
    assert "PRIVATE" not in output.read_text()
    if os.name == "posix":
        assert output.stat().st_mode & 0o077 == 0
    report = json.loads(capsys.readouterr().out)
    assert report["source_snapshots"] == 1
    assert report["historical_completeness"] == "unknown"
    assert report["source_timestamp_timezone"] == "operator_declared_not_provider_verified"
    assert report["checkpoint_advanced"] is False
    assert report["automatic_append_performed"] is False
    assert report["business_outcome_verified"] is False
    second = FakeOpener()
    assert main(cli_args(tmp_path, path, "repeat.csv"), opener=second) == 0
    with (tmp_path / "repeat.csv").open(newline="", encoding="utf-8") as handle:
        assert list(csv.DictReader(handle)) == rows


def test_no_opt_in_or_revoked_grant_prevents_any_source_request(tmp_path, monkeypatch):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    for args in (cli_args(tmp_path, path, consent=False),
                 cli_args(tmp_path, path, tenant="another-team")):
        fake = FakeOpener()
        assert main(args, opener=fake) == 2
        assert not fake.calls
    path.unlink()
    fake = FakeOpener()
    assert main(cli_args(tmp_path, path), opener=fake) == 2
    assert not fake.calls


def test_grant_rechecked_after_network_before_publishing(tmp_path, monkeypatch):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    fake = FakeOpener(on_open=path.unlink)
    assert main(cli_args(tmp_path, path), opener=fake) == 2
    assert len(fake.calls) == 1
    assert not (tmp_path / "selected.csv").exists()


def test_grant_rechecked_immediately_before_publish(tmp_path, monkeypatch):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    from server import servicenow_incident_sync as sync
    original = sync.project_csv

    def revoke_after_projection(*args, **kwargs):
        result = original(*args, **kwargs)
        path.unlink()
        return result

    monkeypatch.setattr(sync, "project_csv", revoke_after_projection)
    assert main(cli_args(tmp_path, path), opener=FakeOpener()) == 2
    assert not (tmp_path / "selected.csv").exists()


@pytest.mark.parametrize("fake", [
    FakeOpener(status=401),
    FakeOpener(status=302),
    FakeOpener(content_type="text/html"),
    FakeOpener(final_url="https://other.service-now.com/api/now/table/incident"),
    FakeOpener(body=b'{"result":[],"result":[]}'),
    FakeOpener(body=b'{"error":"access denied"}'),
    FakeOpener(body=b"x" * (262144 + 1)),
    FakeOpener(rows=[row("c" * 32)]),
], ids=["unauthorized", "redirect-status", "html", "wrong-origin",
        "duplicate-json-key", "error-envelope", "oversize", "unselected"])
def test_http_response_fails_closed_without_csv(tmp_path, monkeypatch, fake, capsys):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    assert main(cli_args(tmp_path, path), opener=fake) == 2
    assert not (tmp_path / "selected.csv").exists()
    result = capsys.readouterr().out
    assert TOKEN not in result and "PRIVATE" not in result


def test_since_filter_is_bound_to_request_and_response(tmp_path, monkeypatch):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    fake = FakeOpener(rows=[row(updated="2026-10-29 00:00:00")])
    assert main(cli_args(tmp_path, path, since="2026-10-30 00:00:00"), opener=fake) == 2
    assert not (tmp_path / "selected.csv").exists()
    assert "sys_updated_on%3E%3D2026-10-30" in fake.calls[0]


def test_timezone_gap_and_fold_refused_without_silent_utc_assumption(tmp_path, monkeypatch):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    for stamp in ("2026-03-08 02:30:00", "2026-11-01 01:30:00"):
        fake = FakeOpener(rows=[row(updated=stamp)])
        assert main(cli_args(tmp_path, path, tz="America/New_York"), opener=fake) == 2
        assert not (tmp_path / "selected.csv").exists()


def test_existing_csv_not_overwritten_and_credential_not_printed(tmp_path, monkeypatch, capsys):
    path = grant(tmp_path)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    output = tmp_path / "selected.csv"
    output.write_text("existing content")
    assert main(cli_args(tmp_path, path), opener=FakeOpener()) == 2
    assert output.read_text() == "existing content"
    assert TOKEN not in capsys.readouterr().out


def test_missing_or_invalid_bearer_refuses_before_network(tmp_path, monkeypatch):
    path = grant(tmp_path)
    for token in (None, "", "bad\nAuthorization: secret", "x" * 4097):
        if token is None:
            monkeypatch.delenv("OWG_SERVICENOW_TOKEN", raising=False)
        else:
            monkeypatch.setenv("OWG_SERVICENOW_TOKEN", token)
        fake = FakeOpener()
        assert main(cli_args(tmp_path, path), opener=fake) == 2
        assert not fake.calls


def test_namespace_isolation_and_unknown_missing_selected_incident(tmp_path):
    a = scope(tenant="team-a", ids=(A, B))
    b = scope(tenant="team-b", ids=(A, B))
    from server.servicenow_incident_staging import plan_page, project_planned_page
    raw = json.dumps({"result": [row()]}).encode()
    first = project_planned_page(a, raw, plan=plan_page(a, limit=16))
    second = project_planned_page(b, raw, plan=plan_page(b, limit=16))
    csv_a, report_a = project_csv(a, first, timezone_name="UTC")
    csv_b, report_b = project_csv(b, second, timezone_name="UTC")
    assert csv_a != csv_b
    assert report_a["unobserved_selected_incidents"] == 1
    assert report_b["unobserved_selected_incidents"] == 1


def test_transport_defaults_to_pinned_https_no_proxy_and_no_redirect():
    opener = _http_opener()
    assert any(type(handler) is request.ProxyHandler and handler.proxies == {} for handler in opener.handlers)
    assert any(type(handler).__name__ == "_NoRedirect" for handler in opener.handlers)
    assert any(type(handler) is request.HTTPSHandler for handler in opener.handlers)


def test_invalid_grant_file_and_foreign_identity_refused(tmp_path):
    path = grant(tmp_path, host="other.service-now.com")
    fake = FakeOpener()
    with pytest.raises(SyncError):
        read_selected_once(scope(), grant_file=path, token=TOKEN, opener=fake)
    assert not fake.calls
    path.write_text('{"version":1,"version":1}')
    path.chmod(0o600)
    with pytest.raises(SyncError):
        read_selected_once(scope(), grant_file=path, token=TOKEN, opener=fake)
    assert not fake.calls
