"""Deployment preflight, readiness and durable worker recovery."""
import json
import os
from pathlib import Path
import time

import pytest

from gateway.connector_jobs import Claim, Readiness, Change, JobError
from server import enterprise_connector_deployment as deployment
from server import enterprise_connector_worker as worker_module
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import local_scheduler as scheduler
from test_enterprise_connector_worker import deployed


pytestmark=pytest.mark.skipif(os.name!="posix",reason="dedicated POSIX worker")


def test_local_preflight_never_claims_reads_or_creates_outbox(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    report=deployment.preflight(worker)
    assert report["ready_for_review"]
    assert report["execution_authorized"] is False
    assert report["lease_claimed"] is False
    assert not calls and not reads and not worker.directory.exists()
    assert jobs.inventory(worker.org)["jobs"][0]["worker_readiness"] is None


def test_remote_preflight_is_exact_and_nondispatching(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    before=jobs.inventory(worker.org)["jobs"][0]
    r=deployment.preflight(worker,remote=True)
    assert r["ready_for_review"] and r["gateway_contact_attempted"]
    assert [path.rsplit("/",1)[1] for path,_ in calls]==["inspect"]
    after=jobs.inventory(worker.org)["jobs"][0]
    assert before==after and not reads
    worker.digest="a"*64
    r=deployment.preflight(worker,remote=True)
    assert not r["ready_for_review"]
    assert any(x["code"]=="gateway_inspection_failed_or_pin_mismatch" for x in r["checks"])


def test_absent_workspace_is_not_initialized(deployed,tmp_path,monkeypatch):
    worker,jobs,p,calls,reads,loss=deployed
    missing=tmp_path/"does-not-exist"
    monkeypatch.setattr(ws,"DATA_DIR",missing)
    r=deployment.preflight(worker)
    assert not r["ready_for_review"] and not missing.exists()
    assert not calls and not reads


def test_preflight_schedule_conflict_and_local_pause(deployed,monkeypatch):
    worker,jobs,p,calls,reads,loss=deployed
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=15,
                     confirm_scheduled_runs=True,confirm_rest_read_retention=True,
                     expected_rest_profile_revision=p["revision"])
    monkeypatch.setattr(scheduler,"_recording",lambda:False)
    r=deployment.preflight(worker)
    codes={x["code"] for x in r["checks"] if x["state"]=="blocked"}
    assert {"local_schedule_conflict","local_recording_paused"}<=codes
    assert not calls and not reads


def test_readiness_reports_local_block_without_lease(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    worker.report_readiness=True
    rest.change(p["profile_id"],p["revision"],False)
    result=worker.once()
    assert result["code"]=="local_profile_unavailable"
    assert [path.rsplit("/",1)[1] for path,_ in calls]==["readiness"]
    j=jobs.inventory(worker.org)["jobs"][0]
    assert j["worker_readiness"]["code"]=="local_profile_unavailable"
    assert not j["worker_readiness_stale"]
    assert j["lease_expires_at"] is None and j["last_receipt"] is None
    assert not reads


def test_recording_pause_readiness_is_not_job_completion(deployed,monkeypatch):
    worker,jobs,p,calls,reads,loss=deployed
    worker.report_readiness=True
    monkeypatch.setattr(scheduler,"_recording",lambda:False)
    assert worker.once()["code"]=="local_recording_paused"
    assert len(calls)==1 and calls[0][0].endswith("/readiness")
    j=jobs.inventory(worker.org)["jobs"][0]
    assert j["worker_readiness"]["code"]=="local_recording_paused"
    assert j["last_receipt"] is None and j["enabled"] and not reads


def test_heartbeat_does_not_authorize_or_mutate_dispatch(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    original=jobs.inventory(worker.org)["jobs"][0]
    clock=[jobs.clock()]
    jobs.clock=lambda:clock[0]
    first=jobs.readiness(worker.token,Readiness(**worker.pin(),code="ready"))
    assert first["dispatch_authorized"] is False
    assert first["recorded"]["independently_verified"] is False
    clock[0]+=10
    second=jobs.readiness(worker.token,Readiness(**worker.pin(),code="local_cooldown"))
    assert second["recorded"]["code"]=="ready"
    clock[0]+=21
    third=jobs.readiness(worker.token,Readiness(**worker.pin(),code="local_cooldown"))
    assert third["recorded"]["code"]=="local_cooldown"
    j=third["job"]
    for field in ("revision","enabled","next_due_at","sequence","last_receipt","lease_expires_at"):
        assert j[field]==original[field]
    clock[0]+=121
    assert jobs.inventory(worker.org)["jobs"][0]["worker_readiness_stale"]
    with pytest.raises(JobError,match="worker_profile_pin_mismatch"):
        jobs.readiness(worker.token,Readiness(**{**worker.pin(),"profile_revision":p["revision"]+1},code="ready"))
    with pytest.raises(JobError,match="worker_credential_required"):
        jobs.inspect_worker("invalid",Claim(**worker.pin()))


def test_readiness_is_cleared_on_worker_rotation(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    jobs.readiness(worker.token,Readiness(**worker.pin(),code="ready"))
    j=jobs.inventory(worker.org)["jobs"][0]
    paused=jobs.change(worker.org,worker.job,Change(expected_revision=j["revision"],enabled=False,
                                                   confirm_change=True),"owner")
    result=jobs.rotate(worker.org,worker.job,paused["revision"],True,"owner")
    assert result["job"]["worker_readiness"] is None
    with pytest.raises(JobError,match="worker_credential_required"):
        jobs.readiness(worker.token,Readiness(**worker.pin(),code="ready"))


def intent(worker):
    return {"phase":"intent","job":worker.job,"organization":worker.org,
            "profile_sha256":worker.digest,"receipt":{"lease_id":"a"*64,"job_revision":1}}


@pytest.mark.parametrize("kind",["symlink","hardlink","public","fifo","oversized","duplicate_key","wrong_pin","invalid_phase"])
def test_invalid_outbox_is_never_replayed_or_overwritten(deployed,kind):
    worker,jobs,p,calls,reads,loss=deployed
    worker.directory.mkdir(mode=0o700)
    path=worker.directory/"pending.json"
    if kind=="symlink":
        real=worker.directory/"other";real.write_text(json.dumps(intent(worker)));real.chmod(0o600);path.symlink_to(real)
    elif kind=="hardlink":
        real=worker.directory/"other";real.write_text(json.dumps(intent(worker)));real.chmod(0o600);os.link(real,path)
    elif kind=="fifo":
        os.mkfifo(path,0o600)
    else:
        state=intent(worker)
        if kind=="wrong_pin":state["profile_sha256"]="b"*64
        if kind=="invalid_phase":state["phase"]="unknown"
        path.write_text(json.dumps(state) if kind not in ("oversized","duplicate_key") else
                        ("x"*9000 if kind=="oversized" else '{"phase":"intent","phase":"receipt"}'))
        path.chmod(0o644 if kind=="public" else 0o600)
    with pytest.raises(worker_module.WorkerError):
        worker.once()
    assert os.path.lexists(path) and not reads and not calls
    assert not deployment.preflight(worker)["ready_for_review"]


def test_pending_intent_preflight_reports_but_does_not_clear(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    worker.directory.mkdir(mode=0o700)
    path=worker.directory/"pending.json"
    worker_module._atomic(path,intent(worker))
    r=deployment.preflight(worker)
    assert not r["ready_for_review"] and path.exists()
    assert any(x["code"]=="pending_intent_requires_reconciliation" for x in r["checks"])
    assert "a"*64 not in json.dumps(r) and not calls and not reads


def test_explicit_capture_gate_refuses_missing_and_corrupt_state(deployed,tmp_path,monkeypatch):
    from shared import capture_control
    worker,jobs,p,calls,reads,loss=deployed
    path=tmp_path/"capture-control.json"
    monkeypatch.setattr(capture_control,"state_path",lambda:path)
    worker.require_explicit_recording=True
    worker.report_readiness=True
    assert worker.once()["code"]=="local_recording_paused"
    path.write_text('{"version":1,"state":"recording","state":"paused"}');path.chmod(0o600)
    assert not worker.recording()
    path.write_text('{"version":1,"state":"recording"}')
    assert worker.recording()
    path.write_text('{"version":1,"state":"stopped"}')
    assert not worker.recording()
    assert not reads


def test_remote_routes_require_job_token(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from gateway.enterprise_app import create_enterprise_app
    from gateway.settings import GatewaySettings
    from gateway.hardening import HardeningSettings
    monkeypatch.setenv("OWG_GATEWAY_ADMIN_TOKEN_API","enabled")
    app=create_enterprise_app(settings=GatewaySettings(database_url="sqlite:///"+str(tmp_path/"gateway.db"),
         admin_token="test-admin",enrollment_token="test-enroll"),hardening=HardeningSettings())
    with TestClient(app) as c:
        pin={"profile_id":"rest_"+"a"*24,"profile_revision":1,"profile_sha256":"b"*64}
        for path,body in (("inspect",pin),("readiness",{**pin,"code":"ready"})):
            r=c.post("/v1/connector-worker/"+path,json=body,headers={"Authorization":"Bearer test-admin"})
            assert r.status_code==401
