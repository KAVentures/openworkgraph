"""Customer worker round trip and lost-reply recovery with actual local event retention."""
from datetime import timedelta
import json
import os

import httpx
import pytest

from gateway.connector_jobs import Jobs, Create, Claim, Receipt, JobError, profile_digest
from gateway.db import GatewayDB
from server import enterprise_rest_feed as rest
from server import local_scheduler as scheduler
from server.enterprise_connector_worker import Worker, WorkerError, _atomic, base_url
from server import event_log_workspace as workspace
from test_enterprise_rest_feed import config, event, page, provider, URL, ENV, TOKEN


@pytest.fixture
def deployed(tmp_path,monkeypatch):
    data=tmp_path/"local"
    data.mkdir()
    monkeypatch.setattr(workspace,"DATA_DIR",data)
    monkeypatch.setattr(scheduler,"DATA_DIR",data)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.setattr(scheduler,"_recording",lambda:True)
    p=rest.create(config())
    db=GatewayDB("sqlite:///"+str(tmp_path/"gateway.db"));db.init()
    jobs=Jobs(db);jobs.initialize()
    made=jobs.create(Create(organization_id="customer",name="Approved REST",provider="rest",
        profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=profile_digest(p),
        interval_minutes=15,confirm_dispatch=True,confirm_counts_only_receipts=True),"admin:fixture")
    calls=[];loss={"complete":False}
    def dispatch(request):
        token=request.headers["Authorization"].removeprefix("Bearer ")
        body=json.loads(request.content)
        assert not any(key in body for key in ("config","source_token","raw_events","provider_payload"))
        calls.append((request.url.path,body))
        try:
            if request.url.path.endswith("/claim"):
                value=jobs.claim(token,Claim.model_validate(body))
            else:
                value=jobs.complete(token,Receipt.model_validate(body))
                if loss["complete"]:
                    loss["complete"]=False
                    raise httpx.ReadTimeout("synthetic response loss")
            return httpx.Response(200,json=value)
        except JobError as exc:
            return httpx.Response(exc.status,json={"detail":exc.code})
    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(dispatch),**kwargs)
    worker=Worker(gateway="https://gateway.example.test",token=made["worker_token"],organization="customer",
        job=made["job"]["job_id"],profile_id=p["profile_id"],revision=p["revision"],digest=profile_digest(p),
        outbox=tmp_path/"outbox",client_factory=factory)
    raw_run=rest.run;reads=[]
    def run(*args,**kwargs):
        def fetch(request):
            reads.append(request.url)
            return page([event(p)])
        return raw_run(*args,**kwargs,client_factory=provider(fetch))
    monkeypatch.setattr(rest,"run",run)
    return worker,jobs,p,calls,reads,loss


def test_round_trip_commits_local_event_and_counts_only_gateway(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    result=worker.once()
    assert result["outcome"]=="succeeded"
    assert len(reads)==1
    assert workspace.list_logs()["logs"][0]["events"]==1
    j=jobs.inventory("customer")["jobs"][0]
    assert j["last_receipt"]["events_added"]==1 and j["last_receipt"]["checkpoint"]
    assert j["last_receipt"]["code"]=="window_committed"
    assert not (worker.directory/"pending.json").exists()
    assert worker.once()["outcome"]=="idle" and len(reads)==1
    raw=json.dumps(calls)+json.dumps(j)
    for private in (TOKEN,ENV,URL,"raw-event-1","raw-case-1","raw-user-1"):
        assert private not in raw


def test_lost_completion_response_replays_exact_receipt_without_source_read(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    loss["complete"]=True
    with pytest.raises(WorkerError,match="gateway_transport_failure"):
        worker.once()
    pending=worker.directory/"pending.json"
    assert pending.exists()
    assert json.loads(pending.read_text())["phase"]=="receipt"
    assert pending.stat().st_mode & 0o077==0
    assert workspace.list_logs()["logs"][0]["events"]==1
    result=worker.once()
    assert result["receipt_replayed"] and len(reads)==1
    assert not pending.exists()
    assert jobs.inventory("customer")["jobs"][0]["last_receipt"]["events_added"]==1
    assert sum(path.endswith("/claim") for path,_ in calls)==1


def test_interrupted_intent_never_reissues_source_read(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    claim=jobs.claim(worker.token,Claim(profile_id=p["profile_id"],profile_revision=p["revision"],profile_sha256=worker.digest))
    worker.directory.mkdir(mode=0o700)
    _atomic(worker.directory/"pending.json",{"phase":"intent","job":worker.job,"organization":worker.org,
        "profile_sha256":worker.digest,"receipt":{"lease_id":claim["lease_id"],"job_revision":claim["job"]["revision"]}})
    assert worker.once()["code"]=="local_run_uncertain"
    assert reads==[]
    assert jobs.inventory("customer")["jobs"][0]["paused_reason"]=="worker_uncertain"


def test_local_conflicting_schedule_and_revision_do_not_claim(deployed):
    worker,jobs,p,calls,reads,loss=deployed
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=15,confirm_scheduled_runs=True,
                     confirm_rest_read_retention=True,expected_rest_profile_revision=p["revision"])
    assert worker.once()["code"]=="local_schedule_conflict"
    assert calls==[] and reads==[]
    rest.change(p["profile_id"],p["revision"],False)
    assert worker.once()["code"]=="local_profile_unavailable"
    assert calls==[]


def test_recording_pause_never_reads_provider(deployed,monkeypatch):
    worker,jobs,p,calls,reads,loss=deployed
    monkeypatch.setattr(scheduler,"_recording",lambda:False)
    assert worker.once()["code"]=="local_recording_paused"
    assert reads==[]
    assert jobs.inventory("customer")["jobs"][0]["last_receipt"]["outcome"]=="skipped"


@pytest.mark.parametrize("value",["http://example.test","https://user:pass@example.test","https://example.test/x",
                                   "https://example.test?token=x","file:///tmp/gateway"])
def test_worker_gateway_url_rejects_unsafe_authority(value):
    with pytest.raises(WorkerError):
        base_url(value)
