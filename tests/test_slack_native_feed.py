"""Native Slack provider contracts and retained workspace transactions."""
from datetime import timedelta
import json

import httpx
import pytest

from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler
from server import slack_native_feed as slack

ENV="OWG_SLACK_TOKEN_FIXTURE"
TOKEN="xoxp-synthetic-audit-token"
ORG="E123ABC"
TEAM="T123ABC"
CHANNEL="C123ABC"
USER="U123ABC"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setenv(ENV,TOKEN)


def config(mode="enterprise_audit",**overrides):
    since=(rest._now()-timedelta(days=2)).replace(microsecond=0)
    audit=mode=="enterprise_audit"
    return {"name":"Slack fixture","mode":mode,"credential_env":ENV,
            "enterprise_id":ORG,"team_id":None if audit else TEAM,
            "channel_id":None if audit else CHANNEL,"case_basis":"entity" if audit else "thread",
            "actions":[],"actor_id":None,"initial_since":rest._iso(since),
            "window_hours":24,"overlap_minutes":60,"ingestion_lag_minutes":15,"page_size":5,
            "confirm_configuration":True,"confirm_retention":True,"confirm_org_audit":audit,
            "confirm_transient_message_content":not audit,**overrides}


def audit_event(p,identity="audit-1",*,at=None,action="file_uploaded",entity="F123ABC"):
    start=rest._stamp(p["config"]["initial_since"])
    at=at or start+timedelta(hours=1)
    return {"id":identity,"date_create":slack._epoch_us(at)//1000000,"action":action,
            "actor":{"type":"user","user":{"id":USER,"name":"private-user-name","email":"private@example.com"}},
            "entity":{"type":"file","file":{"id":entity,"name":"private-file-name"}},
            "context":{"location":{"type":"enterprise","id":ORG,"name":"private-org-name"},
                       "ip_address":"192.0.2.123"},"details":{"secret":"private-audit-details"}}


def message(p,*,at=None,thread=None,subtype=None):
    at=at or rest._stamp(p["config"]["initial_since"])+timedelta(hours=1,microseconds=123456)
    item={"type":"message","ts":slack._decimal_ts(at),"user":USER,
          "text":"private-message-body","files":[{"title":"private-file-title"}],"attachments":[{"text":"private-attachment"}]}
    if thread is not None:
        item["thread_ts"]=slack._decimal_ts(thread)
    if subtype is not None:
        item["subtype"]=subtype
    return item


def auth(**extra):
    return httpx.Response(200,json={"ok":True,"enterprise_id":ORG,"team_id":TEAM,"user_id":USER,**extra})


def channel(**extra):
    return httpx.Response(200,json={"ok":True,"channel":{"id":CHANNEL,"is_member":True,"is_ext_shared":False,
        "is_shared":False,"is_org_shared":False,"is_im":False,"is_mpim":False,"is_channel":True,**extra}})


def page(items,cursor=None,*,audit=True,**extra):
    return httpx.Response(200,json={**({} if audit else {"ok":True}),"entries" if audit else "messages":items,
        "response_metadata":{"next_cursor":cursor or ""},**extra})


def factory(handler):
    def make(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return make


def run(p,handler,**kwargs):
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory(handler),**kwargs)


def route(handler,*,identity=None,channel_response=None):
    def wrapped(request):
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        if str(request.url.copy_with(query=None))==slack.AUTH:
            return auth() if identity is None else identity()
        if str(request.url.copy_with(query=None))==slack.INFO:
            return channel() if channel_response is None else channel_response()
        return handler(request)
    return wrapped


def test_audit_native_paging_overlap_and_exact_retained_id():
    p=slack.create(config(actions=["file_uploaded"],actor_id=USER))
    start=rest._stamp(p["config"]["initial_since"])
    a=audit_event(p,at=start+timedelta(hours=23,minutes=30))
    b=audit_event(p,"audit-2",at=start+timedelta(hours=23,minutes=40))
    calls=[]
    def fetch(request):
        calls.append(request)
        assert request.url.host=="api.slack.com"
        assert request.url.path=="/audit/v1/logs"
        assert request.url.params["actor"]==USER
        assert request.url.params["action"]=="file_uploaded"
        assert request.url.params["oldest"]==str(slack._epoch_us(start)//1000000)
        return page([a,b]) if request.url.params.get("cursor") else page([a],"next")
    first=run(p,route(fetch))
    assert len(calls)==2 and first["events_added"]==2
    assert first["last_result"]["requests"]==4
    assert first["last_result"]["duplicate_records_in_window"]==1
    assert first["last_result"]["scope_readback_verified"]
    assert rest._stamp(first["checkpoint"])==start+timedelta(days=1)
    c=audit_event(p,"audit-3",at=start+timedelta(hours=25))
    second=run(first,route(lambda _:page([b,a,c])))
    assert second["events_added"]==1 and second["duplicates_skipped"]==2
    assert workspace.list_logs()["logs"][0]["events"]==3
    with workspace._LOCK:
        db=workspace._connect()
        try:
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(db.execute("SELECT * FROM objects").fetchall())
        finally:
            db.close()
    assert "slack.audit.file_uploaded" in raw
    for secret in ("audit-1","private-file-name","private-user-name","private@example.com","192.0.2.123","private-audit-details",USER,"F123ABC"):
        assert secret not in raw


def test_channel_activity_microsecond_precision_and_thread_grouping():
    p=slack.create(config("channel_activity"))
    start=rest._stamp(p["config"]["initial_since"])
    at=start+timedelta(hours=23,minutes=30,microseconds=123456)
    a=message(p,at=at)
    b=message(p,at=at+timedelta(microseconds=1),thread=at)
    def fetch(request):
        assert request.url.path=="/api/conversations.history"
        assert request.url.params["channel"]==CHANNEL
        assert request.url.params["inclusive"]=="true"
        assert request.url.params["oldest"]==slack._decimal_ts(start)
        return page([a,b],audit=False)
    first=run(p,route(fetch))
    assert first["events_added"]==2
    assert first["last_result"]["requests"]==5
    assert not first["last_result"]["thread_replies_separately_traversed"]
    assert not first["last_result"]["message_content_retained"]
    with workspace._LOCK:
        db=workspace._connect()
        try:
            rows=db.execute("SELECT ts,activity FROM events ORDER BY ts").fetchall()
            cases=db.execute("SELECT object_id FROM objects").fetchall()
            assert len(cases)==1
            assert rows[1][0]-rows[0][0]==timedelta(microseconds=1)
            raw=str(db.execute("SELECT * FROM events").fetchall())+str(cases)
        finally:
            db.close()
    for secret in ("private-message-body","private-file-title","private-attachment",CHANNEL,USER):
        assert secret not in raw
    second=run(first,route(lambda _:page([a,b],audit=False)))
    assert second["events_added"]==0 and second["duplicates_skipped"]==2


@pytest.mark.parametrize("overrides",[
    {"confirm_org_audit":False},{"confirm_retention":False},{"enterprise_id":None},
    {"actions":["user_login","user_login"]},{"actions":["invalid action"]},
    {"team_id":TEAM},{"case_basis":"thread"},{"credential_env":"HOME"},
    {"window_hours":1,"overlap_minutes":60},{"page_size":True},
])
def test_bad_audit_config_refused(overrides):
    with pytest.raises(rest.FeedError):
        slack.create(config(**overrides))


@pytest.mark.parametrize("overrides",[
    {"confirm_transient_message_content":False},{"team_id":None},{"channel_id":"D123ABC"},
    {"case_basis":"actor"},{"actor_id":USER},{"actions":["user_login"]},
])
def test_bad_channel_config_refused(overrides):
    with pytest.raises(rest.FeedError):
        slack.create(config("channel_activity",**overrides))


@pytest.mark.parametrize("bad",[
    {"is_ext_shared":True},{"is_shared":True},{"is_org_shared":True},
    {"is_member":False},{"is_im":True},{"id":"COTHER"},{"is_ext_shared":None},
])
def test_scope_refuses_shared_direct_or_unverified_channels(bad):
    p=slack.create(config("channel_activity"))
    with pytest.raises(rest.FeedError,match="slack_channel_scope_not_verified"):
        run(p,route(lambda _:pytest.fail("history must not be read"),channel_response=lambda:channel(**bad)))
    assert workspace.list_logs()["logs"]==[]


def test_wrong_tenant_and_wrong_workspace_refuse_before_provider_data():
    p=slack.create(config())
    with pytest.raises(rest.FeedError,match="slack_enterprise_mismatch"):
        run(p,route(lambda _:pytest.fail("audit must not be read"),identity=lambda:auth(enterprise_id="EOTHER")))
    p=slack.create(config("channel_activity"))
    with pytest.raises(rest.FeedError,match="slack_workspace_mismatch"):
        run(p,route(lambda _:pytest.fail("history must not be read"),identity=lambda:auth(team_id="TOTHER")))


def test_late_scope_change_rolls_back_whole_window():
    p=slack.create(config("channel_activity"))
    checks=iter([auth(),auth(user_id="UOTHER")])
    with pytest.raises(rest.FeedError,match="slack_principal_changed"):
        run(p,route(lambda _:page([message(p)],audit=False),identity=lambda:next(checks)))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_empty_success_checkpoint_and_inclusive_boundary():
    p=slack.create(config())
    first=run(p,route(lambda _:page([])))
    assert first["log_id"] is None and first["checkpoint"] is not None
    boundary=rest._stamp(first["checkpoint"])+timedelta(days=1)
    result=run(first,route(lambda _:page([audit_event(p,at=boundary)])),
               now=boundary+timedelta(hours=2))
    assert result["events_added"]==1
    assert rest._stamp(result["checkpoint"])==boundary


def test_audit_oversized_tie_page_not_silently_truncated():
    p=slack.create(config(page_size=1))
    items=[audit_event(p,"audit-"+str(i)) for i in range(3)]
    result=run(p,route(lambda _:page(items)))
    assert result["events_added"]==3


@pytest.mark.parametrize("mode,code",[
    ("enterprise_audit","invalid_or_repeated_provider_cursor"),
    ("channel_activity","slack_more_without_cursor"),
])
def test_incomplete_pagination_never_advances(mode,code):
    p=slack.create(config(mode))
    if mode=="enterprise_audit":
        handler=lambda _:page([audit_event(p)],"same")
    else:
        handler=lambda _:page([message(p)],audit=False,has_more=True)
    with pytest.raises(rest.FeedError,match=code):
        run(p,route(handler))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_rate_limit_and_redirect_are_safe():
    p=slack.create(config())
    with pytest.raises(rest.FeedError,match="provider_rate_limited"):
        run(p,route(lambda _:httpx.Response(429,headers={"Retry-After":"120"})))
    with pytest.raises(rest.FeedError,match="provider_retry_cooldown"):
        run(p,lambda _:pytest.fail("cooldown must prevent network"))
    p=slack.create(config())
    with pytest.raises(rest.FeedError,match="provider_http_failure"):
        run(p,route(lambda _:httpx.Response(302,headers={"Location":"https://evil.example/"})))


def test_mid_window_credential_revocation_and_scheduler_pause(monkeypatch):
    p=slack.create(config())
    def revoke(_):
        monkeypatch.delenv(ENV)
        return page([audit_event(p)])
    with pytest.raises(rest.FeedError,match="slack_credential_unavailable"):
        run(p,route(revoke))
    assert workspace.list_logs()["logs"]==[]
    monkeypatch.setenv(ENV,TOKEN)
    job=scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=15,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=1)
    monkeypatch.setenv(ENV,"xoxp-different-secret")
    monkeypatch.setattr(scheduler,"_recording",lambda:True)
    result=scheduler.run_due(now=rest.time.time()+1000)
    assert result[0]["outcome"]=="paused:rest_profile_changed"
    assert not scheduler.status()["schedules"][0]["enabled"]


def test_audit_filter_and_foreign_location_mismatch_refused():
    p=slack.create(config(actions=["user_login"]))
    with pytest.raises(rest.FeedError,match="slack_action_filter_mismatch"):
        run(p,route(lambda _:page([audit_event(p)])))
    p=slack.create(config())
    item=audit_event(p)
    item["context"]["location"]["id"]="EOTHER"
    with pytest.raises(rest.FeedError,match="slack_enterprise_mismatch"):
        run(p,route(lambda _:page([item])))


def test_conflicting_retained_source_payload_fails_closed():
    p=slack.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    item=audit_event(p,at=start+timedelta(hours=23,minutes=50))
    first=run(p,route(lambda _:page([item])))
    changed={**item,"action":"file_deleted"}
    with pytest.raises(rest.FeedError,match="retention_or_source_failed"):
        run(first,route(lambda _:page([changed])))
    assert rest.inventory()["profiles"][0]["checkpoint"]==first["checkpoint"]


def test_inventory_is_passive_and_does_not_export_scope_or_secret(monkeypatch):
    p=slack.create(config())
    monkeypatch.setattr(httpx.Client,"stream",lambda *a,**kw:pytest.fail("inventory must not read Slack"))
    r=ops.inventory()
    provider=next(x for x in r["providers"] if x["provider"]=="slack")
    assert provider["retained_profiles_observed"]==1
    row=next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])
    assert row["provider"]=="slack" and row["provider_live_health"]=="unknown"
    for secret in (TOKEN,ENV,ORG,TEAM,CHANNEL,USER):
        assert secret not in json.dumps(r)
