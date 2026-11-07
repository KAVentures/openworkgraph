"""Signed run-report channels, version inventory and incident triage (#370 R18)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from shared import automation_report_signing as signing
from server import automation_incidents as incidents, automation_monitoring as am, automation_report_channels as channels

ROOT = Path(__file__).resolve().parents[1]
SPEC = "a" * 64


def report(run_ids, status="succeeded", spec=SPEC, failing="Charge card"):
    deployment = {"name": "Billing agent", "runtime": "openai-agents"}
    if spec:
        deployment["workflow_spec_sha256"] = spec
    steps = [] if status == "succeeded" else [{"step_id": "s1", "activity": failing,
                                               "status": "failed" if status == "failed" else "handed_to_human",
                                               "at": "2024-03-01T10:00:30Z"}]
    return {"format": am.FORMAT, "deployment": deployment, "runs": [
        {"run_id": r, "started_at": "2024-03-01T10:00:00Z", "finished_at": "2024-03-01T10:01:00Z", "status": status,
         "steps": steps, "outcome": {"verified": True, "source": "CLIENT_ASSERTION"}} for r in run_ids]}


def envelope(doc, label="v1.2.0"):
    return json.dumps({"format": channels.ENVELOPE, "version_label": label, "report": doc}).encode()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for module in (am, incidents, channels):
        monkeypatch.setattr(module, "DATA_DIR", tmp_path)
    dep = am.import_report(report(["seed"]))["deployment"]["deployment_id"]
    made = channels.create(dep)
    return dep, made["channel_id"], made["secret"]


def send(cid, secret, seq, body, ts=None):
    h = signing.headers(secret, seq, body, ts)
    return channels.ingest(cid, h["X-OWG-Report-Timestamp"], h["X-OWG-Report-Sequence"], h["X-OWG-Report-Signature"], body)


def test_signature_is_standard_hmac_and_verification_codes():
    body = b'{"x":1}'
    h = signing.headers("00" * 32, 3, body, timestamp=1000)
    assert signing.verify("00" * 32, "1000", "3", h["X-OWG-Report-Signature"], body, now=1000) is None
    assert signing.verify("00" * 32, "1000", "3", h["X-OWG-Report-Signature"], body + b" ", now=1000) == "bad_signature"
    assert signing.verify("00" * 32, "1000", "4", h["X-OWG-Report-Signature"], body, now=1000) == "bad_signature"
    assert signing.verify("00" * 32, "1000", "3", h["X-OWG-Report-Signature"], body, now=2000) == "stale_timestamp"
    assert signing.verify("00" * 32, "x", "3", "v1=00", body) == "malformed_headers"
    too_large = signing.MAX_SEQUENCE + 1
    signed_large = signing.headers("00" * 32, too_large, body, timestamp=1000)
    assert signing.verify("00" * 32, "1000", str(too_large),
                          signed_large["X-OWG-Report-Signature"], body, now=1000) == "malformed_headers"
    boundary = signing.headers("00" * 32, signing.MAX_SEQUENCE, body, timestamp=1000)
    assert signing.verify("00" * 32, "1000", str(signing.MAX_SEQUENCE),
                          boundary["X-OWG-Report-Signature"], body, now=1000) is None


def test_secret_is_shown_once_and_one_live_channel_per_deployment(setup, tmp_path):
    dep, cid, secret = setup
    assert len(secret) == 64
    listed = channels.listing()["channels"][0]
    assert "secret" not in listed and listed["secret_shown_again"] is False
    with pytest.raises(channels.ChannelError):
        channels.create(dep)
    with pytest.raises(channels.ChannelError):
        channels.create("dep_" + "0" * 16)
    if os.name == "posix":
        assert (tmp_path / "automation_report_channels.json").stat().st_mode & 0o777 == 0o600


def test_signed_reports_import_and_exact_resends_are_idempotent(setup):
    dep, cid, secret = setup
    body = envelope(report(["r1", "r2"]))
    first = send(cid, secret, 1, body)
    assert first["outcome"] == "imported" and first["runs_imported"] == 2 and first["business_writes_replayed"] is False
    again = send(cid, secret, 1, body)
    assert again["duplicate"] is True and again["stored_again"] is False
    assert am.deployment_references()[0]["reported_run_count"] == 3
    [version] = channels.listing()["channels"][0]["versions"]
    assert version["version_label"] == "v1.2.0" and version["workflow_spec_sha256"] == SPEC and version["runs"] == 2


def test_tampering_replay_and_stale_requests_are_refused_and_become_incidents(setup):
    dep, cid, secret = setup
    body = envelope(report(["r1"]))
    h = signing.headers(secret, 1, body)
    with pytest.raises(channels.ChannelError) as err:
        channels.ingest(cid, h["X-OWG-Report-Timestamp"], "1", h["X-OWG-Report-Signature"], body.replace(b"r1", b"r9"))
    assert err.value.status == 401
    with pytest.raises(channels.ChannelError):
        send(cid, secret, 1, body, ts=int(time.time()) - 3600)
    send(cid, secret, 1, body)
    with pytest.raises(channels.ChannelError) as err:
        send(cid, secret, 1, envelope(report(["other"])))
    assert err.value.status == 409
    kinds = {(i["kind"], i["fingerprint"]) for i in incidents.listing()["incidents"]}
    assert {("signature_rejected", "bad_signature"), ("signature_rejected", "stale_timestamp"),
            ("replay_conflict", "sequence:1")} <= kinds
    with pytest.raises(channels.ChannelError) as err:
        send(cid, "11" * 32, 2, body)
    assert err.value.status == 401


def test_gaps_are_lost_reports_until_they_arrive(setup):
    dep, cid, secret = setup
    send(cid, secret, 1, envelope(report(["r1"])))
    out = send(cid, secret, 4, envelope(report(["r4"])))
    assert out["new_missing_sequences"] == 2
    state = channels.listing()["channels"][0]
    assert state["missing_sequences"] == [2, 3] and state["highest_sequence"] == 4
    [lost] = [i for i in incidents.listing()["incidents"] if i["kind"] == "lost_reports"]
    assert lost["occurrences"] == 2
    send(cid, secret, 2, envelope(report(["r2"])))
    assert channels.listing()["channels"][0]["missing_sequences"] == [3]


def test_large_signed_sequence_jump_keeps_only_bounded_gap_ids(setup):
    dep, cid, secret = setup
    send(cid, secret, 1, envelope(report(["r1"])))
    big = 100_000_000
    accepted = send(cid, secret, big, envelope(report(["r_big"])))
    assert accepted["outcome"] == "imported"
    assert accepted["new_missing_sequences"] == big - 2
    channel = channels.listing()["channels"][0]
    assert channel["highest_sequence"] == big
    assert channel["missing_count"] == channels.MAX_MISSING
    assert channel["missing_truncated"] is True
    assert channel["missing_sequences"] == list(
        range(big - channels.MAX_MISSING, big - channels.MAX_MISSING + 50))
    lost = next(i for i in incidents.listing()["incidents"] if i["kind"] == "lost_reports")
    assert lost["occurrences"] == big - 2, "inferred gap count, not retained IDs"

    # Late arrival recovers an explicitly retained gap without clearing the
    # warning that much older missing IDs were necessarily discarded.
    late = send(cid, secret, big - 1, envelope(report(["r_late"])))
    assert late["outcome"] == "imported"
    after = channels.listing()["channels"][0]
    assert after["missing_count"] == channels.MAX_MISSING - 1
    assert after["missing_truncated"] is True


def test_a_different_spec_is_version_drift_not_a_silent_rebind(setup):
    dep, cid, secret = setup
    body = envelope(report(["r1"], spec="b" * 64), label="v2.0.0")
    with pytest.raises(channels.ChannelError) as err:
        send(cid, secret, 1, body)
    assert err.value.status == 409
    assert am.deployment_references()[0]["reported_spec_sha256"] == SPEC
    [drift] = [i for i in incidents.listing()["incidents"] if i["kind"] == "version_drift"]
    assert drift["fingerprint"] == "spec:" + "b" * 64 and drift["detail"]["version_label"] == "v2.0.0"
    repeat = send(cid, secret, 1, body)
    assert repeat["duplicate"] and repeat["outcome"] == "refused_version_drift"


def test_failures_group_into_incidents_with_a_review_lifecycle(setup):
    dep, cid, secret = setup
    send(cid, secret, 1, envelope(report(["f1", "f2"], status="failed")))
    send(cid, secret, 2, envelope(report(["h1"], status="handed_to_human", failing="Approve refund")))
    listed = incidents.listing(deployment_id=dep)
    by_kind = {i["kind"]: i for i in listed["incidents"]}
    failure = by_kind["run_failures"]
    assert failure["fingerprint"] == "step:Charge card" and failure["occurrences"] == 2
    assert set(failure["sample_refs"]) == {"f1", "f2"} and by_kind["run_handoffs"]["fingerprint"] == "step:Approve refund"
    with pytest.raises(incidents.IncidentError):
        incidents.triage(failure["incident_id"], {"status": "resolved", "expected_last_seen": "stale", "expected_review_revision": 0})
    incidents.triage(failure["incident_id"], {"status": "acknowledged", "note": "Card processor outage", "expected_review_revision": 0})
    done = incidents.triage(failure["incident_id"], {"status": "resolved", "note": "Processor recovered",
                                                     "expected_last_seen": failure["last_seen"], "expected_review_revision": 1})
    assert done["status"] == "resolved" and len(done["notes"]) == 2 and done["automation_rerun"] is False
    send(cid, secret, 3, envelope(report(["f3"], status="failed")))
    reopened = next(i for i in incidents.listing()["incidents"] if i["incident_id"] == failure["incident_id"])
    assert reopened["status"] == "open" and reopened["reopened_count"] == 1 and reopened["occurrences"] == 3


def test_revoked_and_rotated_channels(setup):
    dep, cid, secret = setup
    rotated = channels.rotate(cid)
    with pytest.raises(channels.ChannelError):
        send(cid, secret, 1, envelope(report(["r1"])))
    send(cid, rotated["secret"], 1, envelope(report(["r1"])))
    channels.revoke(cid)
    with pytest.raises(channels.ChannelError) as err:
        send(cid, rotated["secret"], 2, envelope(report(["r2"])))
    assert err.value.status == 403
    assert channels.create(dep)["channel_id"] != cid


def test_real_http_routes(tmp_path):
    code = r'''
import json
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from shared import automation_report_signing as signing
REPORT = json.loads(DOC)
with TestClient(app) as client:
    owner = {"Authorization": "OWG-Session " + local_auth.create_dashboard_session()}
    bearer = {"Authorization": "Bearer " + local_auth.ensure_api_token(), "Content-Type": "application/json"}
    made = client.post("/v1/automation-monitoring/reports", json=REPORT,
                       headers={**owner, "x-owg-local-storage-confirmed": "true"}).json()
    dep = made["deployment"]["deployment_id"]
    assert client.post(f"/v1/automation-monitoring/deployments/{dep}/channel", headers=bearer).status_code == 403
    channel = client.post(f"/v1/automation-monitoring/deployments/{dep}/channel", headers=owner).json()
    assert len(channel["secret"]) == 64
    body = json.dumps({"format": "openworkgraph.signed-run-report.v1", "version_label": "v1", "report": REPORT}).encode()
    path = f"/v1/automation-monitoring/channels/{channel['channel_id']}/reports"
    assert client.post(path, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    unsigned = client.post(path, content=body, headers=bearer)
    assert unsigned.status_code == 401, unsigned.text
    signed = client.post(path, content=body, headers={**bearer, **signing.headers(channel["secret"], 1, body)})
    assert signed.status_code == 200 and signed.json()["outcome"] == "imported", signed.text
    assert "CLIENT_ASSERTION" not in client.get("/v1/automation-monitoring/channels", headers=owner).text
    assert client.get("/v1/automation-monitoring/incidents", headers=bearer).status_code == 403
    listed = client.get("/v1/automation-monitoring/incidents", headers=owner).json()
    failure = next(i for i in listed["incidents"] if i["kind"] == "run_failures")
    triaged = client.post(f"/v1/automation-monitoring/incidents/{failure['incident_id']}/triage", headers=owner,
                          json={"status": "acknowledged", "note": "Looking", "expected_review_revision": failure["review_revision"]})
    assert triaged.status_code == 200 and triaged.json()["status"] == "acknowledged"
    gone = client.post(f"/v1/automation-monitoring/deployments/{dep}/delete", headers=owner).json()
    assert gone["report_channels_deleted"] == 1 and gone["incidents_deleted"] >= 2
'''
    env = os.environ.copy()
    env.update({"WORKFLOW_OBSERVER_DATA": str(tmp_path / "data"), "WORKFLOW_OBSERVER_AUTH_DIR": str(tmp_path / "auth"),
                "WORKFLOW_OBSERVER_CONFIG": str(tmp_path / "config.json")})
    script = f"DOC = {json.dumps(json.dumps(report(['f1'], status='failed')))}\n" + code
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"stdout={result.stdout}\nstderr={result.stderr}"
