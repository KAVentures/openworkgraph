"""R10 private signal-notification delivery: explicit consent, freshness, and no source data."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from server import signal_notification_delivery as notices
from server import local_scheduler as scheduler
from server import event_log_signal_inbox as inbox

LOG = "log_" + "a" * 16
STATE_A = "a" * 64
STATE_B = "b" * 64
URL = "https://example.com/hooks/owner-reviewed-alerts"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(notices, "_path", lambda: tmp_path / "signal_delivery_channels.json")
    monkeypatch.setattr(scheduler, "scheduled", lambda kind, target: kind == "event_log_signals" and target == LOG)
    items = [{"rule_id": "sig_" + "1" * 16, "name": "private-diagnosis-label",
              "status": "unreviewed", "requires_review": True,
              "state_fingerprint": STATE_A},
             {"rule_id": "sig_" + "2" * 16, "name": "private-customer-name",
              "status": "unreviewed", "requires_review": True,
              "state_fingerprint": STATE_B}]
    monkeypatch.setattr(inbox, "list_inbox", lambda log_id: {"items": deepcopy(items)})
    return items


def make():
    return notices.configure(LOG, URL, confirm_external_delivery=True, confirm_counts_only=True)


def test_no_delivery_by_default_and_two_independent_confirmations():
    assert notices.status(LOG)["configured"] is False
    assert notices.any_enabled() is False
    assert notices.queue_current(LOG) == {"queued": 0, "delivery_enabled": False}
    assert notices.deliver_due() == {"attempted": 0, "sent": 0}
    for external, counts in [(False, True), (True, False), (1, True), (True, 1)]:
        with pytest.raises(notices.DeliveryError, match="confirm"):
            notices.configure(LOG, URL, confirm_external_delivery=external, confirm_counts_only=counts)
    assert not notices._path().exists()
    result = make()
    assert result["enabled"] and result["recipient_host"] == "example.com"
    assert len(result["notification_secret"]) == 64
    assert "notification_secret" not in notices.status(LOG)
    assert URL not in json.dumps(notices.status(LOG))
    if os.name == "posix":
        assert notices._path().stat().st_mode & 0o777 == 0o600


def test_delivery_contains_only_opaque_id_and_count_and_is_hmac_signed(isolated):
    created = make()
    assert notices.queue_current(LOG)["queued"] == 2
    assert notices.queue_current(LOG)["queued"] == 0  # same exact states never flood queue
    sent = []
    def transport(url, body, headers):
        sent.append((url, body, headers))
        return 204

    result = notices.deliver_due(transport=transport)
    assert result["sent"] == 1 and result["unreviewed_signal_states"] == 2
    assert len(sent) == 1 and sent[0][0] == URL
    payload = json.loads(sent[0][1])
    assert payload["format"] == notices.PAYLOAD_FORMAT
    assert payload["unreviewed_signal_states"] == 2
    assert payload["case_data_included"] is False and payload["execution_authorized"] is False
    forbidden = [LOG, "sig_", STATE_A, STATE_B, "private-diagnosis-label",
                 "private-customer-name", created["notification_secret"]]
    assert all(value not in sent[0][1].decode() for value in forbidden)
    assert sent[0][2]["X-OWG-Notification-Signature"] == "v1=" + hmac.new(
        bytes.fromhex(created["notification_secret"]), sent[0][1], hashlib.sha256).hexdigest()
    assert notices.queue_current(LOG)["queued"] == 0
    assert notices.deliver_due(transport=transport)["attempted"] == 0
    assert notices.status(LOG)["delivered_notifications"] == 1


@pytest.mark.parametrize("status", ["acknowledged", "snoozed", "stale", "cleared"])
def test_stale_and_reviewed_alerts_are_suppressed_before_any_http(isolated, status):
    make()
    notices.queue_current(LOG)
    for row in isolated:
        row["status"] = status
        row["requires_review"] = False
    called = []
    result = notices.deliver_due(transport=lambda *args: called.append(args) or 200)
    assert result["suppressed_stale_or_reviewed"] is True
    assert notices.status(LOG)["pending_notifications"] == 0
    assert called == []


def test_changed_state_after_success_queues_once_and_requires_fresh_review(isolated):
    make()
    notices.queue_current(LOG)
    assert notices.deliver_due(transport=lambda *_: 200)["sent"] == 1
    isolated[0]["state_fingerprint"] = "c" * 64
    isolated[1]["status"] = "cleared"
    isolated[1]["requires_review"] = False
    assert notices.queue_current(LOG)["queued"] == 1
    assert notices.deliver_due(transport=lambda *_: 201)["sent"] == 1
    assert notices.status(LOG)["delivered_notifications"] == 2


def test_failed_delivery_retains_pending_and_pauses_after_five(isolated):
    make()
    notices.queue_current(LOG)
    seen = []
    def failed(url, body, headers):
        seen.append((body, headers["X-OWG-Notification-ID"]))
        return 503

    for attempt in range(5):
        current = notices._read()["channels"][LOG]["pending"][0]["next_attempt_epoch"] + 1
        result = notices.deliver_due(now=current, transport=failed)
        assert result["retry_pending"] is True
    assert len(set(body for body, _ in seen)) == 1
    assert len(set(notification_id for _, notification_id in seen)) == 1
    assert notices.status(LOG)["enabled"] is False
    assert notices.status(LOG)["pending_notifications"] == 1
    assert notices.deliver_due(transport=lambda *_: 200)["attempted"] == 0
    with pytest.raises(notices.DeliveryError):
        notices.resume(LOG, confirm_external_delivery=False)
    notices.resume(LOG, confirm_external_delivery=True)
    assert notices.deliver_due(transport=lambda *_: 200)["sent"] == 1
    assert notices.status(LOG)["pending_notifications"] == 0


def test_revocation_erases_keys_and_pending_and_does_not_send(isolated):
    created = make()
    notices.queue_current(LOG)
    assert notices.remove(LOG) == {"removed": True, "pending_erased": True, "secret_erased": True}
    assert notices.status(LOG)["configured"] is False
    assert created["notification_secret"] not in notices._path().read_text()
    assert notices.deliver_due(transport=lambda *_: (_ for _ in ()).throw(AssertionError()))["sent"] == 0


@pytest.mark.parametrize("unsafe", [
    "http://evil.example/hooks",
    "http://127.0.0.1:9000/hooks",
    "https://127.0.0.1:8443/hooks",
    "https://user:password@example.com/hooks",
    "https://example.com/hooks?token=value",
    "https://example.com/hooks#private",
    "file:///etc/passwd",
    "https://192.168.1.1/hooks",
    "https://169.254.169.254/latest/meta-data",
    "https://localhost%0d%0aexample.com/hooks",
    "https://example.com//other",
    "https://example.com/%2f%2fadmin",
])
def test_reject_invalid_or_unsafe_receiver_destinations(unsafe):
    with pytest.raises(notices.DeliveryError):
        notices._url(unsafe)


def test_receiver_dns_rebinding_to_private_subnet_is_blocked(monkeypatch):
    # Protect the actual connection, not merely a preliminary hostname check.
    monkeypatch.setattr(notices.socket, "getaddrinfo",
        lambda host, port, **kwargs: [(2, 1, 6, "", ("10.11.12.13", port))])
    with pytest.raises(notices.DeliveryError, match="restricted address"):
        notices._send(URL, b"{}", {})
    # A valid HTTPS URL is not authorization to contact internal networks.


def test_local_route_requires_dashboard_session_and_explicit_delivery_consent(tmp_path):
    code = r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
path='/v1/schedules/signal-delivery/log_'+'a'*16
with TestClient(app) as client:
    assert client.get(path).status_code==401
    bearer={'Authorization':'Bearer '+local_auth.ensure_api_token()}
    assert client.get(path,headers=bearer).status_code in (401,403)
    owner={'Authorization':'OWG-Session '+local_auth.create_dashboard_session()}
    r=client.get(path,headers=owner)
    assert r.status_code==200,r.text
    assert r.json()['configured'] is False
    assert client.post(path,json={'receiver_url':'https://example.com/hooks'},headers=owner).status_code==422
    assert client.post(path+'/remove',json={'confirm_remove':False},headers=owner).status_code==403
    assert client.post(path+'/resume',json={'confirm_external_delivery':False},headers=owner).status_code==409
"""
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"),
                "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    result = subprocess.run([sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
