"""Explicit Jira handoff: no live writes in tests, durable uncertain attempts."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from server import agent_procurement_workforce_jira_handoff as handoff
from server import agent_procurement_workforce_remediation as register
from server import agent_procurement_workforce_remediation_tickets as tickets
from server import agent_procurement_monitor_journal as journal
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_remediation import create,command,scope,forget_request
from test_agent_procurement_workforce_remediation_tickets import ticket_scope,REF,RECORD

CONFIG=("example.atlassian.net","12345678-1234-1234-1234-123456789abc","ABC","10001")


def setup(monkeypatch):
    _,v=create();f=v["findings"][0]
    v=register.change(command(v,f,"assigned"))
    for name,value in zip(("OWG_JIRA_HOST","OWG_JIRA_CLOUD_ID","OWG_JIRA_REMEDIATION_PROJECT",
                           "OWG_JIRA_REMEDIATION_ISSUE_TYPE_ID"),CONFIG):
        monkeypatch.setenv(name,value)
    monkeypatch.setenv("OWG_JIRA_REMEDIATION_WRITE_ENABLED","1")
    monkeypatch.setenv("OWG_JIRA_REMEDIATION_WRITE_TOKEN","separate-test-token")
    monkeypatch.setattr(handoff,"_authorize",lambda *args:None)
    def forbid(*args):pytest.fail("unexpected provider POST")
    monkeypatch.setattr(handoff,"_post",forbid)
    return v,f,{**ticket_scope(v,tickets.status(scope(v)),f),"operation":"create"}


def approved(body):
    p=handoff.preview(body)
    return p,{**body,"expected_plan_sha256":p["plan_sha256"],"confirm_external_write":True}


def test_default_off_and_exact_preview(local_db,monkeypatch):
    v,f,body=setup(monkeypatch)
    monkeypatch.setenv("OWG_JIRA_REMEDIATION_WRITE_ENABLED","0")
    with pytest.raises(ValueError):handoff.preview(body)
    monkeypatch.setenv("OWG_JIRA_REMEDIATION_WRITE_ENABLED","1")
    plan,req=approved(body)
    assert plan["destination"]["project"]=="ABC"
    assert plan["payload"]["fields"]["description"]["type"]=="doc"
    assert "owner_alias" not in str(plan["payload"]) and "CASE-1" not in str(plan["payload"])
    assert handoff.status(scope(v))["attempts"]==[]
    with pytest.raises(ValueError):handoff.dispatch({**req,"confirm_external_write":False})
    with pytest.raises(ValueError):handoff.dispatch({**req,"expected_plan_sha256":"0"*64})
    assert handoff.status(scope(v))["attempts"]==[]


def test_reservation_visible_before_post_and_duplicate_refused(local_db,monkeypatch):
    v,f,body=setup(monkeypatch);plan,req=approved(body)
    def post(p,token):
        assert p==plan
        attempts=handoff.status(scope(v))["attempts"]
        assert attempts[0]["status"]=="attempt_reserved_outcome_unknown"
        return {"status":"provider_reported_created","http_status":201,"issue_key":"ABC-123"}
    monkeypatch.setattr(handoff,"_post",post)
    result=handoff.dispatch(req)
    assert result["issue_key"]=="ABC-123"
    assert not result["automatic_retry_allowed"]
    assert register.status(scope(v))==v
    with pytest.raises(journal.JournalError):handoff.dispatch(req)
    with pytest.raises(journal.JournalError):register.forget(forget_request(v))


def test_uncertain_post_retained_no_retry(local_db,monkeypatch):
    import httpx
    v,f,body=setup(monkeypatch);_,req=approved(body)
    def timeout(*args):raise httpx.ReadTimeout("fake")
    monkeypatch.setattr(handoff,"_post",timeout)
    assert handoff.dispatch(req)["status"]=="unconfirmed"
    with pytest.raises(journal.JournalError):handoff.dispatch(req)


def test_concurrent_dispatch_at_most_one_post(local_db,monkeypatch):
    v,f,body=setup(monkeypatch);_,req=approved(body);calls=[]
    def post(*args):
        calls.append(1)
        return {"status":"provider_reported_created","http_status":201,"issue_key":"ABC-1"}
    monkeypatch.setattr(handoff,"_post",post)
    def run(_):
        try:return handoff.dispatch(req)["http_status"]
        except journal.JournalError as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(run,range(2)))==[201,409]
    assert calls==[1]


def test_authorization_race_refuses_before_reservation(local_db,monkeypatch):
    v,f,body=setup(monkeypatch);_,req=approved(body)
    def authorize(*args):register.change(command(v,f,"investigating"))
    monkeypatch.setattr(handoff,"_authorize",authorize)
    with pytest.raises(journal.JournalError):handoff.dispatch(req)
    assert handoff.status(scope(v))["attempts"]==[]


def test_comment_requires_exact_bound_observed_project(local_db,monkeypatch):
    v,f,body=setup(monkeypatch)
    with pytest.raises(ValueError):handoff.preview({**body,"operation":"comment"})
    monkeypatch.setattr(tickets.source,"resolve_resource_reference",lambda ref:dict(RECORD))
    monkeypatch.setattr(tickets.source,"ensure_api_token",lambda:"local-test-secret")
    t=tickets.bind({**ticket_scope(v,tickets.status(scope(v)),f),"resource_ref":REF,
                    "confirm_operator_mapping":True,"confirm_local_retention":True})
    body={**ticket_scope(v,t,f),"operation":"comment"}
    monkeypatch.setattr(handoff.source,"record_graph",lambda *args,**kwargs:{"record_observed":False})
    with pytest.raises(ValueError):handoff.preview(body)
    def observed(ref,**kwargs):
        assert kwargs["required_source_type"]==("jira","issue",CONFIG[0])
        return {"record_observed":True,"record_identity_complete_within_retained_window":True,
                "record_type":{"provider":"jira","kind":"issue"},
                "source_evidence":kwargs["source_reader"](ref)}
    monkeypatch.setattr(handoff.source,"record_graph",observed)
    plan,req=approved(body)
    assert plan["destination"]["bound_issue_key"]=="ABC-123"
    assert set(plan["payload"])=={"body"}
    monkeypatch.setattr(handoff,"_post",lambda *args:{"status":"provider_reported_comment_added","http_status":201,"comment_id":"42"})
    assert handoff.dispatch(req)["comment_id"]=="42"
    with pytest.raises(journal.JournalError):handoff.dispatch(req)
    with pytest.raises(ValueError):handoff.preview({**body,"operation":"create"})
