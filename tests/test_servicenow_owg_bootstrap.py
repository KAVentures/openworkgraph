"""Selected ServiceNow journal -> real OWG FastAPI/DuckDB import acceptance.

No customer ServiceNow tenant is contacted. The journal uses production SQLite
storage; OWG runs on disposable loopback HTTP with actual REST authorization.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3

import pytest

from test_salesforce_case_history_owg_apply import context, SESSION
from server.servicenow_incident_journal import update_journal, _scope_digest
from server.servicenow_incident_staging import SelectedIncidents
from server.servicenow_owg_bootstrap import (
    BootstrapError, UncertainBootstrap, bootstrap, main, _verified,
)

HOST = "sample.service-now.com"
INCIDENT = "a" * 32


def selected():
    return SelectedIncidents(host=HOST, tenant_scope="team-a",
                             sys_ids=(INCIDENT,), consented=True, grant_current=True)


def make_journal(private):
    grant = private / "grant.json"
    grant.write_text(json.dumps({"version": 1, "enabled": True, "consented": True,
                                 "host": HOST, "tenant_scope": "team-a",
                                 "sys_ids": [INCIDENT]}), encoding="utf-8")
    if os.name == "posix":
        grant.chmod(0o600)
    journal = private / "selected.sqlite"
    case = "snowcase:" + hashlib.sha256(b"case-a").hexdigest()
    event = "snowevent:" + hashlib.sha256(b"event-a").hexdigest()
    rows, added, _ = update_journal(
        selected(), timezone_name="UTC", journal_file=journal, grant_file=grant,
        incoming=[(case, "Incident state: 2", "2026-10-30T02:00:00+00:00", event)],
    )
    assert len(rows) == added == 1
    return grant, journal


def approved(private, port):
    return dict(instance=HOST, tenant="team-a", incident_ids=[INCIDENT],
                grant_file=private / "grant.json", timezone_name="UTC",
                journal=private / "selected.sqlite",
                log_name="Reviewed ServiceNow incidents",
                confirm_log_name="Reviewed ServiceNow incidents", port=port,
                consent_journal_read=True, consent_local_import=True,
                session=SESSION)


def test_real_owg_http_bootstrap_readback_and_followup_append(context, monkeypatch):
    private, port, workspace = context
    grant, journal = make_journal(private)
    receipt = bootstrap(**approved(private, port))
    assert receipt["status"] == "bootstrapped"
    assert receipt["events"] == receipt["journal_snapshots"] == 1
    assert receipt["owg_import_readback_verified"] is True
    assert receipt["provider_read_performed"] is False
    logs = workspace.list_logs()["logs"]
    assert len(logs) == 1 and logs[0]["log_id"] == receipt["log_id"]
    ns = "snow_" + _scope_digest(selected(), "UTC")[:32]
    assert len(logs[0]["source_identity"]["namespaces"]) == 1
    assert logs[0]["source_identity"]["namespaces"][0]["source_namespace"] == ns
    assert logs[0]["source_identity"]["namespaces"][0]["case_namespace"] == ns
    from server.servicenow_owg_append import main as append_main
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    cli = ["--instance", HOST, "--tenant", "team-a", "--incident-id", INCIDENT,
           "--grant-file", str(grant), "--timezone", "UTC", "--journal", str(journal),
           "--log-id", receipt["log_id"], "--confirm-log-id", receipt["log_id"],
           "--port", str(port), "--consent-local-journal-read", "--consent-local-append"]
    assert append_main(cli) == 0
    assert workspace.list_logs()["logs"][0]["events"] == 1
    assert workspace.append_history(receipt["log_id"])["appends"][-1]["duplicate_events_skipped"] == 1


def test_duplicate_scope_and_refusals_do_not_create_another_log(context):
    private, port, workspace = context
    make_journal(private)
    args = approved(private, port)
    with pytest.raises(BootstrapError):
        bootstrap(**{**args, "consent_local_import": False})
    with pytest.raises(BootstrapError):
        bootstrap(**{**args, "confirm_log_name": "Another"})
    with pytest.raises(ValueError):
        bootstrap(**{**args, "tenant": "team-b"})
    with pytest.raises(ValueError):
        bootstrap(**{**args, "session": "bad"})
    assert workspace.list_logs()["logs"] == []
    bootstrap(**args)
    with pytest.raises(BootstrapError, match="already has a local log"):
        bootstrap(**args)
    assert len(workspace.list_logs()["logs"]) == 1
    assert not (private / "selected.sqlite.bootstrap.lock").exists()


def test_revocation_invalid_journal_and_route_auth_refuse_before_import(context, monkeypatch):
    private, port, workspace = context
    grant, journal = make_journal(private)
    args = approved(private, port)
    data = json.loads(grant.read_text())
    data["enabled"] = False
    grant.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        bootstrap(**args)
    data["enabled"] = True
    grant.write_text(json.dumps(data))
    from server import event_log_routes as routes
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda _: False)
    with pytest.raises(BootstrapError):
        bootstrap(**args)
    assert workspace.list_logs()["logs"] == []
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda v: v == SESSION)
    with sqlite3.connect(journal) as db:
        db.execute("UPDATE events SET timestamp='invalid'")
    with pytest.raises(ValueError):
        bootstrap(**args)
    assert workspace.list_logs()["logs"] == []


def test_uncertain_readback_does_not_claim_success_or_retry(context, monkeypatch):
    private, port, workspace = context
    make_journal(private)
    import server.servicenow_owg_bootstrap as module
    original = module._inventory
    calls = 0

    def interrupted(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BootstrapError("unavailable")
        return original(*args)

    monkeypatch.setattr(module, "_inventory", interrupted)
    with pytest.raises(UncertainBootstrap):
        bootstrap(**approved(private, port))
    assert calls == 2
    assert len(workspace.list_logs()["logs"]) == 1
    monkeypatch.setattr(module, "_inventory", original)
    with pytest.raises(BootstrapError, match="already has a local log"):
        bootstrap(**approved(private, port))


def test_concurrent_duplicate_scope_never_certifies_readback():
    namespace = "snow_" + "f" * 32

    def item(log_id):
        return {"log_id": log_id, "events": 1, "rejected_events": 0,
                "source_format": "csv", "source_identity": {"namespaces": [{
                    "source_namespace": namespace, "case_namespace": namespace}]}}

    a, b = "log_" + "a" * 16, "log_" + "b" * 16
    with pytest.raises(BootstrapError, match="Concurrent duplicate"):
        _verified([item(a), item(b)], a, namespace, 1, set())


def test_cli_missing_consent_no_network_or_sensitive_output(context, monkeypatch, capsys):
    private, port, workspace = context
    make_journal(private)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    cli = ["--instance", HOST, "--tenant", "team-a", "--incident-id", INCIDENT,
           "--grant-file", str(private / "grant.json"), "--timezone", "UTC",
           "--journal", str(private / "selected.sqlite"),
           "--log-name", "Reviewed ServiceNow incidents",
           "--confirm-log-name", "Reviewed ServiceNow incidents", "--port", str(port)]
    assert main(cli) == 2
    output = capsys.readouterr().out
    assert json.loads(output)["possible_local_import"] is False
    assert SESSION not in output and INCIDENT not in output
    assert workspace.list_logs()["logs"] == []
