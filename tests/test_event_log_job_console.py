"""Same-session job console, terminal pin and real queue integration."""
import json
import threading
import time

import pytest

from server import event_log_jobs as engine
from server import event_log_job_console as console
from server import event_log_workspace as workspace
from test_event_log_jobs import jobs, log, wait, SPEC


def request(report):
    return {"expected_terminal_review_sha256":report["terminal_review_sha256"],"confirm":True}


def test_owned_inventory_no_cross_session_data_and_terminal_cleanup(jobs):
    a,b=log(),log()
    done=jobs.submit("owner",a,SPEC,source_bound=True)["job_id"]
    assert wait(jobs,a,done)["state"]=="succeeded"
    other=jobs.submit("other",b,SPEC)["job_id"]
    assert wait(jobs,b,other,owner="other")["state"]=="succeeded"
    with workspace._LOCK:
        pending=jobs.submit("owner",b,SPEC)["job_id"]
        report=console.inventory(jobs,"owner")
        assert {j["job_id"] for j in report["jobs"]}=={done,pending}
        assert report["owned_job_count"]==2 and report["terminal_count"]==1
        completed=next(j for j in report["jobs"] if j["job_id"]==done)
        assert completed["result_bytes"]==len(jobs.result("owner",a,done))
        assert completed["source_bound_result_available"]
        assert not report["current_source_verified"] and not report["execution_authorized"]
        assert other not in json.dumps(report)
        assert all(not {"owner","spec","result","error","control"} & j.keys() for j in report["jobs"])
        removed=console.clear_terminal(jobs,"owner",request(report))
        assert removed["removed_terminal_jobs"]==1 and removed["active_jobs_cancelled"]==0
        assert jobs.status("owner",b,pending)["state"] in {"queued","running"}
        assert jobs.result("other",b,other)
        with pytest.raises(engine.JobError):jobs.result("owner",a,done)
    assert wait(jobs,b,pending)["state"]=="succeeded"


def test_terminal_transition_stales_review_no_partial_cleanup(jobs):
    a=log()
    first=jobs.submit("owner",a,SPEC)["job_id"]
    wait(jobs,a,first)
    with workspace._LOCK:
        second=jobs.submit("owner",a,SPEC)["job_id"]
        old=console.inventory(jobs,"owner")
        assert old["terminal_count"]==1
    wait(jobs,a,second)
    with pytest.raises(engine.JobError,match="changed"):
        console.clear_terminal(jobs,"owner",request(old))
    assert jobs.result("owner",a,first) and jobs.result("owner",a,second)
    latest=console.inventory(jobs,"owner")
    results=[]
    def attempt():
        try:results.append(console.clear_terminal(jobs,"owner",request(latest))["removed_terminal_jobs"])
        except engine.JobError as exc:results.append(exc.status)
    threads=[threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:thread.start()
    for thread in threads:thread.join(5)
    assert sorted(results)==[2,409]
    assert console.inventory(jobs,"owner")["owned_job_count"]==0


@pytest.mark.parametrize("body",[None,{},{"confirm":1,"expected_terminal_review_sha256":"a"*64},
    {"confirm":True,"expected_terminal_review_sha256":True},
    {"confirm":True,"expected_terminal_review_sha256":"a"*63},
    {"confirm":True,"expected_terminal_review_sha256":"a"*64,"extra":1}])
def test_bad_cleanup_refused(jobs,body):
    a=log();jid=jobs.submit("owner",a,SPEC)["job_id"];wait(jobs,a,jid)
    with pytest.raises(engine.JobError) as e:console.clear_terminal(jobs,"owner",body)
    assert e.value.status==422
    assert jobs.result("owner",a,jid)


def test_log_deletion_and_expiry_stale_cleanup(jobs,monkeypatch):
    a=log();jid=jobs.submit("owner",a,SPEC)["job_id"];wait(jobs,a,jid)
    old=console.inventory(jobs,"owner")
    workspace.delete_log(a)
    with pytest.raises(engine.JobError,match="changed"):console.clear_terminal(jobs,"owner",request(old))
    b=log();jid=jobs.submit("owner",b,SPEC)["job_id"];wait(jobs,b,jid)
    old=console.inventory(jobs,"owner")
    with jobs._condition:
        jobs._jobs[jid]["expires"]=time.monotonic()-1
    with pytest.raises(engine.JobError,match="changed"):console.clear_terminal(jobs,"owner",request(old))
    assert console.inventory(jobs,"owner")["owned_job_count"]==0


def test_http_session_scope_no_store_and_confirm(jobs):
    from fastapi import APIRouter,FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from server.event_log_job_routes import add_routes
    from server.event_log_routes import _json_body
    router=APIRouter()
    def auth(request):
        if request.headers.get("authorization") not in {"OWG-Session alice","OWG-Session bob"}:
            raise HTTPException(403,"dashboard session required")
    add_routes(router,auth,_json_body)
    app=FastAPI();app.include_router(router)
    a=log()
    one=jobs.submit(engine.session_key("alice"),a,SPEC)["job_id"]
    two=jobs.submit(engine.session_key("bob"),a,SPEC)["job_id"]
    wait(jobs,a,one,owner=engine.session_key("alice"));wait(jobs,a,two,owner=engine.session_key("bob"))
    with TestClient(app) as c:
        url="/v1/event-log-analysis-jobs"
        owner={"Authorization":"OWG-Session alice"}
        assert c.get("/analysis-job-console.js").status_code==200
        assert c.get(url).status_code==403
        assert c.get(url,headers={"Authorization":"Bearer api"}).status_code==403
        response=c.get(url,headers=owner)
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        r=response.json();assert [j["job_id"] for j in r["jobs"]]==[one]
        assert c.post(url+"/clear-terminal",json={**request(r),"confirm":False},headers=owner).status_code==422
        assert c.post(url+"/clear-terminal",json=request(r),headers={"Authorization":"OWG-Session bob"}).status_code==409
        cleared=c.post(url+"/clear-terminal",json=request(r),headers=owner)
        assert cleared.status_code==200 and cleared.json()["removed_terminal_jobs"]==1
        assert jobs.result(engine.session_key("bob"),a,two)
