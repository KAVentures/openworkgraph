"""Actual Salesforce TCP source and production OWG HTTP/DuckDB bootstrap acceptance."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Reuse the existing production-route TCP fixture and Salesforce source fixture.
from test_salesforce_case_history_owg_apply import context, reviewed, SESSION
from server.salesforce_case_history_owg_bootstrap import (
    BootstrapError, UncertainBootstrap, bootstrap, main,
)
from server.salesforce_case_history_owg_apply import apply


def approved(private, port, receipt):
    return dict(
        checkpoint=private / "checkpoint.json",
        manifest=private / "review.json",
        confirm_manifest_sha256=receipt["manifest_sha256"],
        log_name="Reviewed Salesforce history",
        confirm_log_name="Reviewed Salesforce history",
        port=port, consent_prepared_read=True,
        consent_local_import=True, consent_checkpoint_advance=True,
        session=SESSION)


def test_real_source_to_owg_http_import_readback_checkpoint(context):
    private, port, workspace = context
    receipt = reviewed(private)
    result = bootstrap(**approved(private, port, receipt))
    assert result["status"] == "bootstrapped"
    assert result["events"] == 1 and result["rejected_events"] == 0
    assert result["owg_import_readback_verified"] is True
    assert result["checkpoint_advanced"] is True
    logs = workspace.list_logs()["logs"]
    assert len(logs) == 1 and logs[0]["log_id"] == result["log_id"]
    assert logs[0]["events"] == 1
    assert logs[0]["source_identity"]["namespaces"][0]["source_namespace"] == (
        "sf_" + receipt["scope_sha256"][:32])
    assert json.loads((private / "checkpoint.json").read_text())["until"] == (
        "2026-10-03T00:00:00Z")


def test_consent_name_revocation_tamper_refuse_without_write(context, monkeypatch):
    private, port, workspace = context
    receipt = reviewed(private)
    args = approved(private, port, receipt)
    with pytest.raises(BootstrapError):
        bootstrap(**{**args, "consent_local_import": False})
    with pytest.raises(BootstrapError):
        bootstrap(**{**args, "confirm_log_name": "Other"})
    with pytest.raises(BootstrapError):
        bootstrap(**{**args, "session": "invalid"})
    from server import event_log_routes as routes
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda value: False)
    with pytest.raises(BootstrapError):
        bootstrap(**args)
    assert workspace.list_logs()["logs"] == []
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda value: value == SESSION)
    (private / "history.csv").write_text((private / "history.csv").read_text() + "\n")
    with pytest.raises(BootstrapError):
        bootstrap(**args)
    assert workspace.list_logs()["logs"] == []


def test_uncertain_import_readback_recovered_via_existing_apply(context, monkeypatch):
    private, port, workspace = context
    receipt = reviewed(private)
    import server.salesforce_case_history_owg_bootstrap as module
    original = module._logs
    count = 0

    def interrupted(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise BootstrapError("readback unavailable")
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "_logs", interrupted)
    with pytest.raises(UncertainBootstrap):
        bootstrap(**approved(private, port, receipt))
    assert not (private / "checkpoint.json").exists()
    logs = workspace.list_logs()["logs"]
    assert len(logs) == 1 and logs[0]["events"] == 1
    monkeypatch.setattr(module, "_logs", original)
    with pytest.raises(BootstrapError, match="already has a local log"):
        bootstrap(**approved(private, port, receipt))
    log_id = logs[0]["log_id"]
    result = apply(
        checkpoint=private / "checkpoint.json", manifest=private / "review.json",
        confirm_manifest_sha256=receipt["manifest_sha256"],
        log_id=log_id, confirm_log_id=log_id, port=port,
        consent_prepared_read=True, consent_local_append=True,
        consent_checkpoint_advance=True, session=SESSION)
    assert result["checkpoint_advanced"] is True
    assert result["added_events"] == 0
    assert result["duplicate_events_skipped"] == 1
    assert workspace.list_logs()["logs"][0]["events"] == 1


def test_second_same_scope_refused_without_duplicate_log(context):
    private, port, workspace = context
    receipt = reviewed(private)
    bootstrap(**approved(private, port, receipt))
    other = private.parent / "other"
    other.mkdir(mode=0o700)
    for filename in ("review.json", "history.csv"):
        path = other / filename
        path.write_bytes((private / filename).read_bytes())
        path.chmod(0o600)
    with pytest.raises(BootstrapError, match="already has a local log"):
        bootstrap(**approved(other, port, receipt))
    assert len(workspace.list_logs()["logs"]) == 1

def test_concurrent_same_scope_readback_never_certifies_checkpoint():
    from server.salesforce_case_history_owg_bootstrap import _verify_new
    ns = "sf_" + "a" * 32
    def item(log_id):
        return {
            "log_id": log_id, "source_format": "csv",
            "events": 1, "rejected_events": 0,
            "source_identity": {"namespaces": [{
                "source_namespace": ns, "case_namespace": ns}]},
        }
    first, second = "log_" + "1" * 16, "log_" + "2" * 16
    with pytest.raises(BootstrapError, match="Concurrent duplicate"):
        _verify_new([item(first), item(second)], first, ns, 1, set())
