"""Actual OWG FastAPI/DuckDB + SQLite journal incremental connector acceptance.

ServiceNow transport is disposable and provider-shaped; no customer sandbox
or live credentials are accessed. OWG uses its real authenticated REST routes.
"""
from __future__ import annotations

import json
import os

import pytest

from test_salesforce_case_history_owg_apply import context, SESSION
from test_servicenow_incident_journal import FakeTransport, TOKEN, record
from test_servicenow_owg_bootstrap import HOST, INCIDENT, approved, make_journal
from server.servicenow_owg_bootstrap import bootstrap
from server.servicenow_owg_incremental import (
    IncrementalError, UncertainIncremental, main, sync_selected,
)
from server.servicenow_owg_append import AppendError


def setup_source(context):
    private, port, workspace = context
    grant, journal = make_journal(private)
    first = bootstrap(**approved(private, port))
    return private, port, workspace, grant, journal, first["log_id"]


def args(data):
    private, port, _, grant, journal, log_id = data
    return dict(
        instance=HOST, tenant="team-a", incident_ids=[INCIDENT],
        grant_file=grant, timezone_name="UTC", journal=journal,
        log_id=log_id, confirm_log_id=log_id, port=port,
        consent_source_read=True, consent_journal_update=True,
        consent_local_append=True, session=SESSION, token=TOKEN,
    )


def test_real_http_selected_read_journal_and_append_with_replay(context):
    data = setup_source(context)
    provider = FakeTransport()
    first = sync_selected(**args(data), provider_opener=provider)
    assert len(provider.calls) == 1
    assert first["status"] == "verified"
    assert first["provider_read_performed"] is True
    assert first["new_journal_snapshots"] == 1
    assert first["replayed_journal_snapshots"] == 0
    assert first["journal_snapshots"] == 2
    assert first["added_events"] == 1
    assert first["duplicate_events_skipped"] == 1
    assert first["rejected_events"] == 0
    assert first["local_owg_readback_verified"] is True
    assert first["checkpoint_advanced"] is False
    assert first["historical_completeness"] == "unknown"
    assert first["source_writes_performed"] is False
    assert data[2].list_logs()["logs"][0]["events"] == 2
    assert data[2].append_history(data[5])["appends"][-1]["added_events"] == 1

    replay = FakeTransport()
    second = sync_selected(**args(data), provider_opener=replay)
    assert len(replay.calls) == 1
    assert second["new_journal_snapshots"] == 0
    assert second["replayed_journal_snapshots"] == 1
    assert second["added_events"] == 0
    assert second["duplicate_events_skipped"] == 2
    assert data[2].list_logs()["logs"][0]["events"] == 2

    updated = FakeTransport([record(revision="4", state="3",
                                    updated="2026-10-30 04:10:00")])
    third = sync_selected(**args(data), provider_opener=updated,
                          since="2026-10-30 03:10:00")
    assert third["new_journal_snapshots"] == 1
    assert third["added_events"] == 1
    assert data[2].list_logs()["logs"][0]["events"] == 3


@pytest.mark.parametrize("change", [
    "no_source_consent", "no_journal_consent", "no_append_consent",
    "wrong_confirm", "wrong_tenant", "revoked", "wrong_session",
    "missing_journal", "wrong_log",
])
def test_preflight_refuses_before_any_provider_get(context, change):
    data = setup_source(context)
    params = args(data)
    if change == "no_source_consent":
        params["consent_source_read"] = False
    elif change == "no_journal_consent":
        params["consent_journal_update"] = False
    elif change == "no_append_consent":
        params["consent_local_append"] = False
    elif change == "wrong_confirm":
        params["confirm_log_id"] = "log_" + "f" * 16
    elif change == "wrong_tenant":
        params["tenant"] = "team-b"
    elif change == "revoked":
        grant = json.loads(data[3].read_text())
        grant["enabled"] = False
        data[3].write_text(json.dumps(grant))
    elif change == "wrong_session":
        params["session"] = "bad"
    elif change == "missing_journal":
        params["journal"] = data[0] / "missing.sqlite"
    elif change == "wrong_log":
        params["log_id"] = params["confirm_log_id"] = "log_" + "f" * 16
    provider = FakeTransport()
    with pytest.raises((IncrementalError, ValueError)):
        sync_selected(**params, provider_opener=provider)
    assert not provider.calls
    assert data[2].list_logs()["logs"][0]["events"] == 1


def test_revocation_during_source_read_prevents_journal_and_owg_append(context):
    data = setup_source(context)
    provider = FakeTransport(on_open=data[3].unlink)
    with pytest.raises(IncrementalError):
        sync_selected(**args(data), provider_opener=provider)
    assert len(provider.calls) == 1
    assert data[2].list_logs()["logs"][0]["events"] == 1
    assert len(data[2].append_history(data[5])["appends"]) == 0


def test_empty_selected_response_is_non_destructive(context):
    data = setup_source(context)
    provider = FakeTransport(rows=[])
    result = sync_selected(**args(data), provider_opener=provider)
    assert result["status"] == "no_selected_snapshots"
    assert result["local_owg_append_performed"] is False
    assert len(provider.calls) == 1
    assert data[2].list_logs()["logs"][0]["events"] == 1
    assert data[2].append_history(data[5])["appends"] == []


def test_uncertain_post_retains_journal_and_never_auto_retries(context, monkeypatch):
    import server.servicenow_owg_incremental as module
    data = setup_source(context)
    original = module.append_local
    calls = []

    def committed_but_response_lost(*a, **kw):
        calls.append(1)
        original(*a, **kw)
        raise AppendError("Simulated lost receipt after committed append")

    monkeypatch.setattr(module, "append_local", committed_but_response_lost)
    with pytest.raises(UncertainIncremental):
        sync_selected(**args(data), provider_opener=FakeTransport())
    assert len(calls) == 1
    assert data[2].list_logs()["logs"][0]["events"] == 2
    assert len(data[2].append_history(data[5])["appends"]) == 1

    # Operator independently inspects the committed append before any retry.
    history = data[2].append_history(data[5])["appends"]
    assert history[-1]["added_events"] == 1
    monkeypatch.setattr(module, "append_local", original)
    recovery = sync_selected(**args(data), provider_opener=FakeTransport())
    assert recovery["status"] == "verified"
    assert recovery["added_events"] == 0
    assert recovery["duplicate_events_skipped"] == 2
    assert data[2].list_logs()["logs"][0]["events"] == 2


def test_cli_three_consents_env_credentials_and_counts_only(context, monkeypatch, capsys):
    data = setup_source(context)
    private, port, _, grant, journal, log_id = data
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    cmd = [
        "--instance", HOST, "--tenant", "team-a", "--incident-id", INCIDENT,
        "--grant-file", str(grant), "--timezone", "UTC",
        "--journal", str(journal), "--log-id", log_id,
        "--confirm-log-id", log_id, "--port", str(port),
        "--consent-selected-read", "--consent-local-journal-update",
        "--consent-local-append",
    ]
    provider = FakeTransport()
    assert main(cmd, provider_opener=provider) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "verified"
    assert len(provider.calls) == 1
    assert TOKEN not in json.dumps(receipt)
    assert SESSION not in json.dumps(receipt)
    assert INCIDENT not in json.dumps(receipt)
    assert "PRIVATE" not in json.dumps(receipt)

    # Missing one consent refuses without provider network or local append.
    second = FakeTransport()
    assert main(cmd[:-1], provider_opener=second) == 2
    assert not second.calls
    assert json.loads(capsys.readouterr().out)["possible_local_append"] is False


def test_cli_uncertain_post_exit_code_and_private_output(context, monkeypatch, capsys):
    import server.servicenow_owg_incremental as module
    data = setup_source(context)
    params = args(data)
    monkeypatch.setenv("OWG_DASHBOARD_SESSION", SESSION)
    monkeypatch.setenv("OWG_SERVICENOW_TOKEN", TOKEN)
    monkeypatch.setattr(module, "append_local", lambda *a, **kw:
                        (_ for _ in ()).throw(AppendError("PRIVATE TOKEN")))
    cmd = [
        "--instance", HOST, "--tenant", "team-a", "--incident-id", INCIDENT,
        "--grant-file", str(params["grant_file"]), "--timezone", "UTC",
        "--journal", str(params["journal"]), "--log-id", params["log_id"],
        "--confirm-log-id", params["log_id"], "--port", str(params["port"]),
        "--consent-selected-read", "--consent-local-journal-update",
        "--consent-local-append",
    ]
    assert main(cmd, provider_opener=FakeTransport()) == 3
    output = capsys.readouterr().out
    assert json.loads(output)["possible_local_append"] is True
    assert "PRIVATE" not in output and TOKEN not in output and SESSION not in output
    assert data[2].list_logs()["logs"][0]["events"] == 1
