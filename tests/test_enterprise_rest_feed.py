"""Real retained event-log transactions and injected provider HTTP transport."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

URL="https://customer.example.com/events"
TOKEN="private-test-token-123456"
ENV="OWG_REST_TOKEN_TEST"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv("OWG_REST_ALLOWED_ENDPOINTS",json.dumps([URL]))
    monkeypatch.setenv(ENV,TOKEN)


def config(**overrides):
    since=datetime.now(timezone.utc).replace(microsecond=0)-timedelta(days=2)
    return {"name":"Customer events","endpoint":URL,"credential_env":ENV,
            "initial_since":since.isoformat(),"window_hours":24,"overlap_minutes":60,"page_size":5,
            "mapping":{"event_id":"id","case_id":"case","activity":"step","event_time":"time",
                       "change_time":"changed","resource":"actor"},
            "confirm_configuration":True,"confirm_activity_labels":True,**overrides}


def event(p,identity="raw-event-1",*,changed=None,step="Review"):
    since=rest._stamp(p["config"]["initial_since"])
    at=since+timedelta(hours=1)
    return {"id":identity,"case":"raw-case-1","step":step,"time":rest._iso(at),
            "changed":rest._iso(changed or at),"actor":"raw-user-1","unmapped":"never-retain-me"}


def provider(handler):
    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return factory


def page(records,cursor=None):
    return httpx.Response(200,json={"records":records,"next_cursor":cursor})


def run(p,handler,**kwargs):
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=provider(handler),**kwargs)


def test_atomic_bootstrap_overlap_dedupe_and_private_projection():
    p=rest.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    first=event(p,changed=start+timedelta(hours=23,minutes=30))
    other=event(p,"raw-event-2",changed=start+timedelta(hours=23,minutes=40))
    calls=[]
    def fetch(request):
        calls.append(request)
        assert request.url.copy_with(query=None)==httpx.URL(URL)
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        if request.url.params.get("cursor")=="page-2":
            return page([first,other])
        return page([first],"page-2")
    result=run(p,fetch)
    assert len(calls)==2 and result["events_added"]==2
    assert result["last_result"]["duplicate_records_in_window"]==1
    assert rest._stamp(result["checkpoint"])==start+timedelta(hours=24)
    assert result["last_result"]["events_added"]==2
    assert workspace.list_logs()["logs"][0]["events"]==2
    with workspace._LOCK:
        c=workspace._connect()
        try:
            raw=str(c.execute("SELECT * FROM event_source_keys").fetchall())+str(c.execute("SELECT * FROM objects").fetchall())
            assert "raw-event" not in raw and "raw-case" not in raw and "raw-user" not in raw
        finally:
            c.close()
    assert TOKEN not in json.dumps(rest.inventory()) and "credential_binding" not in result["config"]
    third=event(p,"raw-event-3",changed=start+timedelta(hours=26))
    second=run(result,lambda _:page([first,third]))
    assert second["log_id"]==result["log_id"]
    assert second["events_added"]==1 and second["duplicates_skipped"]==1
    assert second["last_result"]["duplicates_skipped"]==1
    assert workspace.list_logs()["logs"][0]["events"]==3


def test_empty_window_advances_without_fabricated_dataset():
    p=rest.create(config())
    result=run(p,lambda _:page([]))
    assert result["events_added"]==0 and result["log_id"] is None
    assert result["checkpoint"] is not None
    assert workspace.list_logs()["logs"]==[]


def test_failed_pagination_no_partial_commit_and_retry_safe():
    p=rest.create(config())
    def fetch(request):
        if request.url.params.get("cursor"):
            return httpx.Response(503,text="secret provider error")
        return page([event(p)],"next")
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,fetch)
    failed=rest.inventory()["profiles"][0]
    assert failed["checkpoint"] is None and failed["log_id"] is None
    assert failed["last_result"]=={"outcome":"failed","error":"provider_http_failure"}
    result=run(p,lambda _:page([event(p)]))
    assert result["events_added"]==1
    assert workspace.list_logs()["logs"][0]["events"]==1


def test_saved_source_identity_conflict_rolls_back():
    p=rest.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    original=event(p,changed=start+timedelta(hours=23,minutes=50))
    first=run(p,lambda _:page([original]))
    changed={**original,"step":"Mutated"}
    with pytest.raises(rest.FeedError,match="retention_or_source_failed"):
        run(first,lambda _:page([changed]))
    assert rest.inventory()["profiles"][0]["checkpoint"]==first["checkpoint"]
    assert workspace.list_logs()["logs"][0]["events"]==1


@pytest.mark.parametrize("response,code",[
    (lambda p:httpx.Response(302,headers={"Location":"https://evil.example.com/steal"}),"provider_http_failure"),
    (lambda p:httpx.Response(200,content=b'{"records":[],"records":[],"next_cursor":null}'),"invalid_provider_json"),
    (lambda p:page([{"id":"x"}]),"missing_mapped_source_field"),
    (lambda p:page([event(p,changed=rest._stamp(p["config"]["initial_since"])-timedelta(seconds=1))]),"source_record_outside_requested_change_window"),
    (lambda p:page([event(p),event(p,step="Different")]),"conflicting_source_event_identity"),
    (lambda p:page([],"same"),"invalid_or_repeated_provider_cursor"),
])
def test_bad_provider_response_refused_without_checkpoint(response,code):
    p=rest.create(config())
    with pytest.raises(rest.FeedError,match=code):
        run(p,lambda _:response(p))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_rate_limit_cooldown_and_no_retry_network(monkeypatch):
    p=rest.create(config())
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,lambda _:httpx.Response(429,headers={"Retry-After":"120"}))
    assert rest.inventory()["profiles"][0]["retry_not_before"]>rest.time.time()
    with pytest.raises(rest.FeedError,match="provider_retry_cooldown"):
        run(p,lambda _:pytest.fail("cooldown must prevent HTTP"))


def test_credential_allowlist_and_disable_recheck_before_retention(monkeypatch):
    p=rest.create(config())
    monkeypatch.setenv(ENV,"changed-secret")
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="credential_changed"):
        run(p,lambda _:pytest.fail("changed credential must prevent HTTP"))
    monkeypatch.setenv(ENV,TOKEN)
    def disable_during_read(request):
        rest.change(p["profile_id"],p["revision"],False)
        return page([event(p)])
    with pytest.raises(rest.FeedError,match="rest_run_invalidated"):
        run(p,disable_during_read)
    assert workspace.list_logs()["logs"]==[]
    assert rest.inventory()["profiles"][0]["status"]=="disabled"


def test_delete_dataset_prevents_recreation():
    p=rest.create(config())
    result=run(p,lambda _:page([event(p)]))
    workspace.delete_log(result["log_id"])
    current=rest.inventory()["profiles"][0]
    assert current["status"]=="dataset_deleted" and not current["enabled"]
    assert current["log_id"] is None and current["checkpoint"] is None
    with pytest.raises(rest.FeedError,match="retained_dataset_deleted"):
        rest.change(p["profile_id"],current["revision"],True)


def test_interrupted_lease_recovery(monkeypatch):
    p=rest.create(config())
    now=rest._now()
    pending,_,_=rest._begin(p["profile_id"],p["revision"],now)
    with pytest.raises(rest.FeedError,match="rest_run_in_progress"):
        run(p,lambda _:pytest.fail("active lease"))
    c=rest._connect()
    try:
        pending["lease_until"]=0
        rest._put(c,pending)
        c.execute("UPDATE enterprise_ingestion_runs SET lease_until=0 WHERE run_id=?",[pending["run_id"]])
    finally:
        c.close()
    assert rest.inventory()["profiles"][0]["status"]=="interrupted"
    from server import enterprise_routine_reconciliation as recovery
    with pytest.raises(rest.FeedError,match="expired_routine_intent"):
        run(p,lambda _:pytest.fail("unreconciled lease"))
    target={"profile_id":p["profile_id"],"revision":p["revision"],"run_id":pending["run_id"]}
    plan=recovery.preview(target)
    receipt=recovery.apply({**target,"reviewed_at":plan["reviewed_at"],"plan_sha256":plan["plan_sha256"],
        "confirm_exact_intent":True,"confirm_workers_reconciled":True,
        "confirm_uncertain_remote_outcome":True,"confirm_disable_and_reauthorize":True})
    p=rest.change(p["profile_id"],receipt["revision"],True)
    result=run(p,lambda _:page([event(p)]))
    assert result["events_added"]==1


def test_recurring_exact_pin_and_pause_on_revision_change(monkeypatch):
    p=rest.create(config())
    args={"kind":"enterprise_rest_feed","target_id":p["profile_id"],"interval_minutes":15,
          "confirm_scheduled_runs":True}
    with pytest.raises(scheduler.ScheduleError,match="explicit recurring"):
        scheduler.create(**args)
    with pytest.raises(scheduler.ScheduleError,match="profile changed"):
        scheduler.create(**args,confirm_rest_read_retention=True,expected_rest_profile_revision=2)
    job=scheduler.create(**args,confirm_rest_read_retention=True,expected_rest_profile_revision=1)
    assert job["rest_profile_revision"]==1
    rest.change(p["profile_id"],p["revision"],False)
    monkeypatch.setattr(scheduler,"_recording",lambda:True)
    result=scheduler.run_due(now=rest.time.time()+1000)
    assert result[0]["outcome"]=="paused:rest_profile_changed"
    current=scheduler.status()["schedules"][0]
    assert not current["enabled"] and current["paused_reason"]=="rest_profile_changed_requires_reapproval"
    with pytest.raises(scheduler.ScheduleError,match="remove the old schedule"):
        scheduler.update(job["schedule_id"],enabled=True)


@pytest.mark.parametrize("overrides",[
    {"endpoint":"http://customer.example.com/events"},
    {"endpoint":"https://customer.example.com/events?secret=x"},
    {"endpoint":"https://other.example.com/events"},
    {"credential_env":"HOME"},
    {"confirm_configuration":False},
    {"confirm_activity_labels":False},
    {"window_hours":1,"overlap_minutes":60},
    {"page_size":True},
])
def test_configuration_strict_and_deployment_allowlisted(overrides):
    with pytest.raises(rest.FeedError):
        rest.create(config(**overrides))


def test_operations_inventory_never_refreshes_credentials_and_joins_exact_schedule(monkeypatch):
    from server import jira_retained_profiles,connector_oauth
    p=rest.create(config())
    scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=60,confirm_scheduled_runs=True,
                     confirm_rest_read_retention=True,expected_rest_profile_revision=1)
    monkeypatch.setattr(jira_retained_profiles,"status",lambda:pytest.fail("status can refresh OAuth"))
    monkeypatch.setattr(connector_oauth,"status",lambda:{"connections":[{"provider":"atlassian",
        "reauthorization_required":True,"host":"secret-host","scopes":["secret-scope"],"access_token":TOKEN}]})
    r=ops.inventory()
    row=next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="rest" and len(row["schedules"])==1
    assert row["provider_live_health"]=="unknown"
    assert "no_retained_success_timestamp" in row["review_flags"]
    assert not r["provider_reads_performed"] and not r["credential_refresh_performed"]
    serialized=json.dumps(r)
    for private in (URL,TOKEN,"secret-host","secret-scope",ENV):
        assert private not in serialized
    assert next(x for x in r["providers"] if x["provider"]=="atlassian")["oauth_reconnect_required"]==1


def test_real_local_http_dashboard_session_required(tmp_path):
    script=r"""
from fastapi.testclient import TestClient
from server.secure_app import app
from server import local_auth
from test_enterprise_rest_feed import config
with TestClient(app) as c:
    h={"Authorization":"OWG-Session "+local_auth.create_dashboard_session()}
    bearer={"Authorization":"Bearer "+local_auth.ensure_api_token()}
    path='/v1/enterprise-connectors'
    assert c.get(path,headers=bearer).status_code in (401,403)
    assert c.post(path+'/rest',json=config(),headers=bearer).status_code in (401,403)
    r=c.get(path,headers=h)
    assert r.status_code==200,r.text
    assert r.headers["cache-control"]=="no-store"
    made=c.post(path+'/rest',json=config(),headers=h)
    assert made.status_code==200,made.text
    p=made.json()
    assert c.post(path+'/rest/'+p["profile_id"]+'/run',json={"revision":1,"confirm_reads":False,"confirm_retention":True},headers=h).status_code==403
    assert c.post(path+'/rest',content=b'{"name":"a","name":"b"}',headers={**h,"Content-Type":"application/json"}).status_code==422
    assert c.get('/enterprise-connectors').status_code==200
    assert c.get('/enterprise-connectors.js').status_code==200
"""
    env={**os.environ,"WORKFLOW_OBSERVER_DATA":str(tmp_path/"data"),
         "WORKFLOW_OBSERVER_AUTH_DIR":str(tmp_path/"auth"),
         "WORKFLOW_OBSERVER_CONFIG":str(tmp_path/"config.json")}
    proc=subprocess.run([sys.executable,"-c","import sys;sys.path.insert(0,'tests');"+script],
                        cwd=Path(__file__).resolve().parents[1],env=env,capture_output=True,text=True,timeout=120)
    assert proc.returncode==0,proc.stdout+proc.stderr
