"""R18 customer SDK -> signed OWG channel; queue is offline, retries preserve bytes."""
from __future__ import annotations

from io import BytesIO
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from urllib.error import URLError
from zipfile import ZipFile

import pytest

from server import automation_report_channels as channels
from server import automation_monitoring as monitoring
from server import automation_incidents as incidents
from shared.process_agent_project import build_runtime_project
from test_process_agent_project import packet


class Reply:
    def __init__(self, value, status=200):
        self.body = json.dumps(value).encode()
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        pass

    def read(self, _limit):
        return self.body


class Receiver:
    def __init__(self, cid):
        self.cid = cid
        self.calls = []
        self.fail_after_import_once = False

    def open(self, request, timeout):
        assert timeout <= 15
        assert request.full_url.endswith("/" + self.cid + "/reports")
        assert request.get_method() == "POST"
        assert request.get_header("Content-type") == "application/json"
        data = request.data
        headers = {name.lower(): value for name, value in request.header_items()}
        assert headers["authorization"].startswith("Bearer ")
        response = channels.ingest(self.cid, headers["x-owg-report-timestamp"],
                                   headers["x-owg-report-sequence"],
                                   headers["x-owg-report-signature"], data)
        self.calls.append({"body": data, "sequence": int(headers["x-owg-report-sequence"]),
                           "outcome": response})
        if self.fail_after_import_once:
            self.fail_after_import_once = False
            raise URLError("synthetic lost acknowledgement")
        return Reply(response)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for module in (channels, monitoring, incidents):
        monkeypatch.setattr(module, "DATA_DIR", tmp_path / "state")
    dep = monitoring.import_report({
        "format": monitoring.FORMAT,
        "deployment": {"name": "Customer runtime", "runtime": "openai-agents",
                       "workflow_spec_sha256": "a" * 64},
        "runs": [{"run_id": "seed", "started_at": "2024-03-01T10:00:00Z",
                  "finished_at": "2024-03-01T10:01:00Z", "status": "succeeded",
                  "steps": []}]
    })["deployment"]["deployment_id"]
    channel = channels.create(dep)
    with ZipFile(BytesIO(build_runtime_project(packet()))) as z:
        z.extractall(tmp_path / "customer")
    root = tmp_path / "customer"
    monkeypatch.syspath_prepend(str(root))
    module_spec = importlib.util.spec_from_file_location("owg_generated_sender_test", root / "send_report.py")
    sender = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(sender)
    report = root / "run-report.json"
    report.write_text(json.dumps({
        "format": monitoring.FORMAT,
        "deployment": {"name": "Customer runtime", "runtime": "openai-agents",
                       "workflow_spec_sha256": "a" * 64},
        "runs": [{"run_id": "customer-1", "started_at": "2024-03-02T10:00:00Z",
                  "finished_at": "2024-03-02T10:01:00Z",
                  "status": "succeeded", "steps": []}]
    }), encoding="utf-8")
    outbox = root / "report-outbox.json"
    monkeypatch.setenv("OWG_REPORT_RECEIVER_URL",
        f"http://127.0.0.1:8787/v1/automation-monitoring/channels/{channel['channel_id']}/reports")
    monkeypatch.setenv("OWG_REPORT_CHANNEL_SECRET", channel["secret"])
    monkeypatch.setenv("OWG_REPORT_API_TOKEN", "local-api-token-only-for-report-tests")
    return sender, report, outbox, channel, Receiver(channel["channel_id"])


def test_staged_report_does_not_transmit_and_send_requires_opt_in(setup):
    sender, report, outbox, channel, receiver = setup
    with pytest.raises(sender.DeliveryError, match="requires the next sequence"):
        sender.queue(str(report), str(outbox), channel["channel_id"], "v1")
    assert not outbox.exists()
    result = sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    assert result["sent"] is False and result["queued_sequence"] == 1
    assert receiver.calls == []
    with pytest.raises(sender.DeliveryError, match="explicit"):
        sender.send(str(outbox), transport=receiver)
    data = outbox.read_text()
    assert channel["secret"] not in data
    assert "local-api-token-only" not in data
    if os.name == "posix":
        assert outbox.stat().st_mode & 0o777 == 0o600
    accepted = sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert accepted["sequence"] == 1 and accepted["pending"] == 0
    assert len(receiver.calls) == 1
    assert channels.listing()["channels"][0]["accepted_reports"] == 1
    assert channels.listing()["channels"][0]["highest_sequence"] == 1


def test_lost_ack_is_replayed_with_identical_bytes_and_sequence(setup):
    sender, report, outbox, channel, receiver = setup
    sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    receiver.fail_after_import_once = True
    with pytest.raises(sender.DeliveryError, match="pending bytes retained"):
        sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert sender._state(outbox)["pending"][0]["sequence"] == 1
    result = sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert result["duplicate"] is True
    assert len(receiver.calls) == 2
    assert receiver.calls[0]["sequence"] == receiver.calls[1]["sequence"] == 1
    assert receiver.calls[0]["body"] == receiver.calls[1]["body"]
    assert channels.listing()["channels"][0]["accepted_reports"] == 1
    assert sender._state(outbox)["pending"] == []
    assert sender._state(outbox)["next_sequence"] == 2


def test_outbox_sequence_and_payload_corruption_fail_closed(setup):
    sender, report, outbox, channel, receiver = setup
    sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    with pytest.raises(sender.DeliveryError, match="already pending"):
        sender.queue(str(report), str(outbox), channel["channel_id"], "v1")
    with pytest.raises(sender.DeliveryError, match="another channel"):
        sender.queue(str(report), str(outbox), "chn_" + "0" * 16, "v1")
    before = json.loads(outbox.read_text())
    before["pending"][0]["sha256"] = "0" * 64
    outbox.write_text(json.dumps(before))
    with pytest.raises(sender.DeliveryError, match="digest"):
        sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert receiver.calls == []


@pytest.mark.parametrize("url", [
    "http://example.com/v1/automation-monitoring/channels/chn_0000000000000000/reports",
    "ftp://127.0.0.1/v1/automation-monitoring/channels/chn_0000000000000000/reports",
    "https://example.com/evil",
    "https://example.com/v1/automation-monitoring/channels/chn_0000000000000000/reports?token=secret",
    "https://example.com/v1/automation-monitoring/channels/chn_0000000000000000/reports#data",
    "https://user:pass@example.com/v1/automation-monitoring/channels/chn_0000000000000000/reports",
])
def test_report_receiver_rejects_unsafe_url(url, setup):
    sender, _, _, channel, _ = setup
    chosen = channel["channel_id"]
    url = url.replace("chn_0000000000000000", chosen)
    with pytest.raises(sender.DeliveryError):
        sender._destination(url, chosen)


def test_exact_secure_url_and_localhost_endpoint_are_permitted(setup):
    sender, _, _, channel, _ = setup
    chosen = channel["channel_id"]
    local = f"http://127.0.0.1:8787/v1/automation-monitoring/channels/{chosen}/reports"
    secure = f"https://example.org/v1/automation-monitoring/channels/{chosen}/reports"
    assert sender._destination(local, chosen) == local
    assert sender._destination(secure, chosen) == secure


def test_pending_reports_use_distinct_sequences_even_after_failed_delivery(setup):
    sender, report, outbox, channel, receiver = setup
    sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    body = json.loads(report.read_text())
    body["runs"][0]["run_id"] = "customer-2"
    report.write_text(json.dumps(body), encoding="utf-8")
    second = sender.queue(str(report), str(outbox), channel["channel_id"], "v1")
    assert second["queued_sequence"] == 2 and second["pending"] == 2
    assert sender._state(outbox)["next_sequence"] == 3
    receiver.fail_after_import_once = True
    with pytest.raises(sender.DeliveryError):
        sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert [x["sequence"] for x in sender._state(outbox)["pending"]] == [1, 2]
    sender.send(str(outbox), confirm_network=True, transport=receiver)
    second_result = sender.send(str(outbox), confirm_network=True, transport=receiver)
    assert second_result["sequence"] == 2 and second_result["pending"] == 0
    assert channels.listing()["channels"][0]["highest_sequence"] == 2
    assert channels.listing()["channels"][0]["accepted_reports"] == 2


@pytest.mark.parametrize("mutation", [
    lambda r: r.update(credentials={"secret": "must-not-send"}),
    lambda r: r["deployment"].update(api_key="must-not-send"),
    lambda r: r["runs"][0].update(case_input={"customer_id": "must-not-send"}),
    lambda r: r["runs"][0].update(steps=[{"step_id": "step-1", "activity": "Read",
                                         "status": "completed",
                                         "at": "2024-03-02T10:00:01Z",
                                         "tool_arguments": "must-not-send"}]),
    lambda r: r["runs"][0].update(started_at="2024-03-02T10:00:00"),
    lambda r: r["runs"][0].update(run_id="private\nrecord"),
    lambda r: r["runs"][0].update(outcome={"verified": "true", "source": "self"}),
])
def test_unknown_sensitive_report_fields_and_invalid_types_refused_before_queue(setup, mutation):
    sender, report, outbox, channel, receiver = setup
    body = json.loads(report.read_text())
    mutation(body)
    report.write_text(json.dumps(body))
    with pytest.raises(sender.DeliveryError):
        sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    assert not outbox.exists() and receiver.calls == []


def test_local_report_rejects_duplicate_json_and_symlink(tmp_path, setup):
    sender, report, outbox, channel, receiver = setup
    report.write_text('{"format":"openworkgraph.automation-run-report.v1","format":"forged","runs":[]}')
    with pytest.raises(sender.DeliveryError, match="duplicate-field"):
        sender.queue(str(report), str(outbox), channel["channel_id"], "v1", 1)
    if hasattr(os, "symlink"):
        link = tmp_path / "linked-report.json"
        link.symlink_to(report)
        with pytest.raises(sender.DeliveryError, match="non-symlink"):
            sender.queue(str(link), str(outbox), channel["channel_id"], "v1", 1)
    assert receiver.calls == [] and not outbox.exists()

