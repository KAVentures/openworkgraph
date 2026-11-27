"""Cross-channel local signal delivery operations, no network or source writes."""
from __future__ import annotations

from copy import deepcopy
import json
import time

import pytest

from server import signal_delivery_portfolio as portfolio
from server import signal_notification_delivery as notices
from server import local_scheduler as scheduler
from server import event_log_signal_inbox as inbox

A = "log_" + "a" * 16
B = "log_" + "b" * 16
RULE = "sig_" + "1" * 16


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "_path", lambda: tmp_path / "ledger.json")
    monkeypatch.setattr(scheduler, "_path", lambda: tmp_path / "schedule.json")
    monkeypatch.setattr(scheduler, "scheduled", lambda *_: True)
    monkeypatch.setattr(inbox, "list_inbox", lambda _: {"items": [{
        "rule_id": RULE, "name": "PRIVATE_LABEL", "status": "unreviewed",
        "requires_review": True, "state_fingerprint": "c" * 64}]})
    monkeypatch.setattr(notices, "_send", lambda *_: pytest.fail("unexpected network operation"))


def configure(key):
    return notices.configure(key, "https://example.com/PRIVATE_RECEIVER_PATH",
                             confirm_external_delivery=True, confirm_counts_only=True)


def schedule(*keys, enabled=True, quiet=None):
    scheduler._write({"version": 1, "schedules": {f"sch_{i}": {
        "kind": "event_log_signals", "target_id": key, "enabled": enabled,
        "signal_rules_fingerprint": "d" * 64, "quiet_hours": quiet,
        "next_run_epoch": time.time() + 60} for i, key in enumerate(keys)}})


def select(channel, group):
    return {"log_id": channel["log_id"], "channel_id": channel["channel_id"],
            "notification_id": group["notification_id"], "queue_sha256": group["queue_sha256"]}


def seed():
    a, b = configure(A), configure(B)
    notices.queue_current(A)
    notices.queue_current(B)
    schedule(A, B)
    return a, b


def paused():
    seed()
    report = portfolio.overview()
    portfolio.pause_all(expected_enabled_channels_sha256=report["enabled_channels_sha256"], confirm_pause=True)
    return portfolio.overview()


def test_empty_and_schedule_unavailable_are_distinct():
    empty = portfolio.overview()
    assert empty["configured_channels"] == 0
    assert empty["schedule_metadata_status"] == "available"
    assert not notices._path().exists()
    configure(A)
    scheduler._path().write_text("invalid json")
    report = portfolio.overview()
    assert report["schedule_metadata_status"] == "unavailable"
    assert report["channels"][0]["status"] == "schedule_unknown"
    assert report["channels"][0]["schedule_enabled"] is None


def test_overview_exact_counts_and_minimized_output():
    a, b = seed()
    before = notices._path().read_bytes()
    report = portfolio.overview(now=time.time() + 1)
    assert report["configured_channels"] == report["enabled_channels"] == 2
    assert report["pending_groups"] == report["pending_state_occurrences"] == 2
    assert report["status_counts"]["head_due"] == 2
    assert report["previously_attempted_groups"] == 0
    assert notices._path().read_bytes() == before
    serialized = json.dumps(report)
    for forbidden in ("PRIVATE_LABEL", "PRIVATE_RECEIVER_PATH", "example.com", RULE,
                      "c" * 64, a["notification_secret"], b["notification_secret"]):
        assert forbidden not in serialized
    assert not report["source_data_read"] and not report["notifications_sent"]


def test_schedule_missing_paused_quiet_and_rule_approval():
    configure(A)
    assert portfolio.overview()["channels"][0]["status"] == "schedule_missing"
    schedule(A, enabled=False)
    assert portfolio.overview()["channels"][0]["status"] == "schedule_paused"
    schedule(A, quiet={"timezone": "UTC", "start": "00:00", "end": "23:59"})
    # 2026-01-01 at noon UTC.
    assert portfolio.overview(now=1767268800)["channels"][0]["status"] == "quiet_hours"
    schedule(A)
    data = scheduler._read()
    data["schedules"]["sch_0"].pop("signal_rules_fingerprint")
    scheduler._write(data)
    assert portfolio.overview()["channels"][0]["status"] == "rule_approval_missing"


def test_pause_all_preserves_queue_secret_history_and_schedules():
    seed()
    schedule_before = scheduler._path().read_bytes()
    before = notices._read()
    pin = portfolio.overview()["enabled_channels_sha256"]
    result = portfolio.pause_all(expected_enabled_channels_sha256=pin, confirm_pause=True)
    assert result["paused_channels"] == 2 and result["retained_pending_groups"] == 2
    assert scheduler._path().read_bytes() == schedule_before
    after = notices._read()
    for key in (A, B):
        a, b = before["channels"][key], after["channels"][key]
        assert b["enabled"] is False
        for field in ("pending", "seen", "signing_secret", "receiver_url", "consecutive_failures", "delivered"):
            assert a[field] == b[field]
    assert notices.deliver_due() == {"attempted": 0, "sent": 0}
    assert portfolio.overview()["enabled_channels"] == 0


def test_pause_rejects_replaced_or_new_enabled_channel():
    configure(A)
    old = portfolio.overview()["enabled_channels_sha256"]
    configure(B)
    before = notices._path().read_bytes()
    with pytest.raises(notices.DeliveryError, match="changed"):
        portfolio.pause_all(expected_enabled_channels_sha256=old, confirm_pause=True)
    assert notices._path().read_bytes() == before


def test_dismiss_batch_exact_atomic_and_never_acknowledges():
    report = paused()
    selected = [select(c, c["pending_groups"][0]) for c in report["channels"]]
    result = portfolio.dismiss_batch(selections=selected, confirm_dismiss=True)
    assert result["dismissed_groups"] == result["affected_channels"] == 2
    assert result["dismissed_state_occurrences"] == 2
    assert not result["signal_reviews_changed"]
    assert portfolio.overview()["pending_groups"] == 0
    for key in (A, B):
        status = notices.status(key)
        assert status["dismissed_state_keys"] == 1
        assert status["delivered_notifications"] == 0
        assert not status["enabled"]
        assert inbox.list_inbox(key)["items"][0]["requires_review"]
        notices.resume(key, confirm_external_delivery=True)
        assert notices.queue_current(key)["queued"] == 0


@pytest.mark.parametrize("kind", ["stale", "duplicate", "resumed", "capacity"])
def test_failed_batch_never_partially_mutates(kind, monkeypatch):
    report = paused()
    selected = [select(c, c["pending_groups"][0]) for c in report["channels"]]
    if kind == "stale":
        selected[-1]["queue_sha256"] = "0" * 64
    elif kind == "duplicate":
        selected.append(selected[0])
    elif kind == "resumed":
        notices.resume(B, confirm_external_delivery=True)
    else:
        monkeypatch.setattr(notices, "MAX_DISMISSED", 1)
        data = notices._read()
        data["channels"][B]["dismissed"] = ["0" * 64]
        notices._save(data)
    before = notices._path().read_bytes()
    with pytest.raises(notices.DeliveryError):
        portfolio.dismiss_batch(selections=selected, confirm_dismiss=True)
    assert notices._path().read_bytes() == before


def test_save_failure_never_commits_partial_pause_or_dismiss(monkeypatch):
    seed()
    pin = portfolio.overview()["enabled_channels_sha256"]
    before = notices._path().read_bytes()
    def fail(_):
        raise notices.DeliveryError("simulated failure")
    with monkeypatch.context() as m:
        m.setattr(notices, "_save", fail)
        with pytest.raises(notices.DeliveryError):
            portfolio.pause_all(expected_enabled_channels_sha256=pin, confirm_pause=True)
    assert notices._path().read_bytes() == before
    portfolio.pause_all(expected_enabled_channels_sha256=pin, confirm_pause=True)
    report = portfolio.overview()
    selected = [select(c,c["pending_groups"][0]) for c in report["channels"]]
    before = notices._path().read_bytes()
    monkeypatch.setattr(notices, "_save", fail)
    with pytest.raises(notices.DeliveryError):
        portfolio.dismiss_batch(selections=selected, confirm_dismiss=True)
    assert notices._path().read_bytes() == before


def test_attempt_state_preserved_and_shown_after_failure():
    seed()
    notices.deliver_due(transport=lambda *_: 503)
    report = portfolio.overview()
    assert report["previously_attempted_groups"] == 1
    portfolio.pause_all(expected_enabled_channels_sha256=report["enabled_channels_sha256"], confirm_pause=True)
    selected = [select(c,c["pending_groups"][0]) for c in portfolio.overview()["channels"]]
    assert portfolio.dismiss_batch(selections=selected, confirm_dismiss=True)["previously_attempted_groups"] == 1


def test_corrupt_delivery_ledger_never_appears_empty():
    configure(A)
    data = notices._read()
    data["channels"][A]["enabled"] = "true"
    notices._save(data)
    with pytest.raises(notices.DeliveryError):
        portfolio.overview()


def test_authenticated_routes_no_store_and_confirmation(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from server import local_scheduler_routes as routes
    monkeypatch.setattr(routes, "dashboard_session_valid", lambda token: token == "local")
    app = FastAPI()
    app.include_router(routes.router)
    owner = {"Authorization": "OWG-Session local"}
    seed()
    path = "/v1/schedules/signal-delivery-portfolio"
    with TestClient(app) as client:
        assert client.get(path).status_code == 403
        assert client.get(path, headers={"Authorization":"Bearer api"}).status_code == 403
        response = client.get(path, headers=owner)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "no-store"
        body = {"expected_enabled_channels_sha256": response.json()["enabled_channels_sha256"], "confirm_pause": False}
        assert client.post(path+"/pause-all", headers=owner, json=body).status_code == 422
        body["confirm_pause"] = True
        assert client.post(path+"/pause-all", headers=owner, json=body).json()["paused_channels"] == 2
        selected = [select(c,c["pending_groups"][0]) for c in client.get(path,headers=owner).json()["channels"]]
        response = client.post(path+"/dismiss-batch", headers=owner,
                               json={"selections":selected,"confirm_dismiss":True})
        assert response.status_code == 200, response.text
        assert response.json()["dismissed_groups"] == 2
        assert response.headers["cache-control"] == "no-store"
