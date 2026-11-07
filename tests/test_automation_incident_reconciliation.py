"""R18 incident counts come from currently retained distinct runs, not report submissions."""
from __future__ import annotations

import pytest

from server import automation_monitoring as monitoring
from server import automation_monitoring_routes as routes
from server import automation_incidents as incidents
from server import automation_report_channels as channels
from test_automation_report_channels import envelope, report, send


@pytest.fixture
def store(tmp_path, monkeypatch):
    for module in (monitoring, incidents, channels):
        monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    dep = monitoring.import_report(report(["seed"]))["deployment"]["deployment_id"]
    made = channels.create(dep)
    return dep, made["channel_id"], made["secret"]


def _incident(deployment_id, kind="run_failures", fingerprint=None):
    rows = [i for i in incidents.listing(deployment_id=deployment_id)["incidents"]
            if i["kind"] == kind and (fingerprint is None or i["fingerprint"] == fingerprint)]
    assert len(rows) == 1, rows
    return rows[0]


def test_signed_reimports_do_not_inflate_resolved_failures_or_touch_review_time(store, monkeypatch):
    dep, channel, secret = store
    send(channel, secret, 1, envelope(report(["f1", "f2"], status="failed")))
    first = _incident(dep)
    assert first["occurrences"] == 2 and first["current_run_count"] == 2
    assert first["count_basis"] == "distinct_retained_run_ids"
    assert set(first["sample_refs"]) == {"f1", "f2"}
    incidents.triage(first["incident_id"], {"status": "resolved", "note": "Reviewed"})
    after_triage = _incident(dep)
    # Even a valid, separately signed *new sequence* can contain old run IDs.
    repeated = send(channel, secret, 2, envelope(report(["f1", "f2"], status="failed")))
    assert repeated["incidents_updated"] == []
    identical = _incident(dep)
    assert identical["status"] == "resolved"
    assert identical["occurrences"] == 2
    assert identical["reopened_count"] == 0
    assert identical["last_seen"] == after_triage["last_seen"]
    assert len(identical["notes"]) == 1
    resend = send(channel, secret, 2, envelope(report(["f1", "f2"], status="failed")))
    assert resend["duplicate"] is True and resend["stored_again"] is False
    send(channel, secret, 3, envelope(report(["f3"], status="failed")))
    reopened = _incident(dep)
    assert reopened["status"] == "open" and reopened["occurrences"] == 3
    assert reopened["reopened_count"] == 1


def test_manual_reimports_reconcile_and_changed_outcomes_clear_old_group(store):
    dep, channel, secret = store
    first = routes._import_and_triage(report(["r1"], status="failed"), dep)
    assert len(first["incidents_updated"]) == 1
    same = routes._import_and_triage(report(["r1"], status="failed"), dep)
    assert same["incidents_updated"] == []
    assert _incident(dep)["occurrences"] == 1
    corrected = routes._import_and_triage(report(["r1"], status="succeeded"), dep)
    assert corrected["incidents_updated"]
    cleared = _incident(dep)
    assert cleared["current_run_count"] == 0 and cleared["occurrences"] == 0
    assert cleared["sample_refs"] == []
    assert cleared["status"] == "open", "a data correction does not silently resolve human triage"
    assert monitoring.list_deployments()["deployments"][0]["runs"] == 2  # seed + r1


def test_moving_a_run_to_another_failing_activity_updates_both_groups(store):
    dep, _, _ = store
    routes._import_and_triage(report(["r1"], status="failed", failing="Charge card"), dep)
    first = _incident(dep, fingerprint="step:Charge card")
    incidents.triage(first["incident_id"], {"status": "acknowledged", "note": "Investigating"})
    routes._import_and_triage(report(["r1"], status="failed", failing="Send receipt"), dep)
    old = _incident(dep, fingerprint="step:Charge card")
    updated = _incident(dep, fingerprint="step:Send receipt")
    assert old["occurrences"] == 0 and old["status"] == "acknowledged" and old["notes"][0]["note"] == "Investigating"
    assert updated["occurrences"] == 1 and updated["sample_refs"] == ["r1"]
    assert incidents.listing(deployment_id=dep)["counts"]["open"] == 1


def test_same_count_but_different_run_membership_reopens_a_resolved_incident(store):
    dep, _, _ = store
    routes._import_and_triage(report(["r1"], status="failed"), dep)
    incident = _incident(dep)
    incidents.triage(incident["incident_id"], {"status": "resolved"})
    # One run is corrected and a separate run fails: count stays at one.
    routes._import_and_triage(report(["r1"], status="succeeded"), dep)
    assert _incident(dep)["status"] == "resolved" and _incident(dep)["occurrences"] == 0
    routes._import_and_triage(report(["r2"], status="failed"), dep)
    now = _incident(dep)
    assert now["status"] == "open" and now["occurrences"] == 1
    assert now["reopened_count"] == 1 and now["sample_refs"] == ["r2"]


def test_ten_displayed_sample_ids_are_not_used_as_dedupe_ledger(store):
    dep, _, _ = store
    ids = [f"f{i:03d}" for i in range(60)]
    routes._import_and_triage(report(ids, status="failed"), dep)
    before = _incident(dep)
    assert before["occurrences"] == 60 and len(before["sample_refs"]) == 10
    assert before["sample_refs"] == ids[:10]
    assert routes._import_and_triage(report(ids[10:], status="failed"), dep)["incidents_updated"] == []
    after = _incident(dep)
    assert after["run_membership_sha256"] == before["run_membership_sha256"]
    assert after["last_seen"] == before["last_seen"] and after["occurrences"] == 60


def test_legacy_inflation_is_corrected_without_falsely_reopening_human_review(store):
    dep, _, _ = store
    routes._import_and_triage(report(["r1"], status="failed"), dep)
    row = _incident(dep)
    incidents.triage(row["incident_id"], {"status": "resolved", "note": "Done"})
    legacy = incidents._load()
    item = legacy["incidents"][row["incident_id"]]
    item["occurrences"] = 42
    item.pop("current_run_count")
    item.pop("count_basis")
    item.pop("run_membership_sha256")
    incidents._write(legacy)
    changes = incidents.reconcile_deployment(dep)
    repaired = _incident(dep)
    assert changes == [row["incident_id"]]
    assert repaired["occurrences"] == 1 and repaired["current_run_count"] == 1
    assert repaired["status"] == "resolved" and repaired["reopened_count"] == 0
    assert incidents.reconcile_deployment(dep) == []


def test_deleting_deployment_erases_run_receipts_and_triage_notes(store):
    dep, _, _ = store
    routes._import_and_triage(report(["r1"], status="failed"), dep)
    assert _incident(dep)["run_membership_sha256"]
    deleted = routes._delete_with_channels(dep)
    assert deleted["incidents_deleted"] >= 1
    assert incidents.listing(deployment_id=dep)["incidents"] == []
