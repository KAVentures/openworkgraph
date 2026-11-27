"""Google Reports API contract and retained-log acceptance through common lifecycle."""
from datetime import timedelta
import json
from urllib.parse import parse_qs,urlsplit

import httpx
import pytest

from server import connector_oauth as oauth
from server import enterprise_rest_feed as rest
from server import enterprise_connector_operations as ops
from server import google_workspace_audit as google
from server import event_log_workspace as workspace
from server import local_scheduler as scheduler

CONNECTION="conn_"+"a"*16
CUSTOMER="C012abc34"
TOKEN="private-google-token"


@pytest.fixture(autouse=True)
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)
    monkeypatch.setattr(scheduler,"DATA_DIR",tmp_path)
    grants=[{"connection_id":CONNECTION,"provider":"google","scopes":[google.SCOPE],
             "has_refresh_token":True,"reauthorization_required":False}]
    monkeypatch.setattr(oauth,"status",lambda:{"connections":grants})
    monkeypatch.setattr(oauth,"bearer_for",lambda connection,provider:TOKEN)
    return grants


def config(**overrides):
    since=(rest._now()-timedelta(days=2)).replace(microsecond=0)
    return {"name":"Google audit fixture","connection_id":CONNECTION,"customer_id":CUSTOMER,
        "user_key":"all","application":"drive","case_basis":"drive_document",
        "initial_since":rest._iso(since),"window_hours":24,"overlap_minutes":60,
        "ingestion_lag_minutes":60,"page_size":5,"confirm_configuration":True,
        "confirm_audit_retention":True,"confirm_domain_scope":True,"confirm_drive_parameters":True,**overrides}


def item(p,qualifier="123",*,when=None,actor="123456789012345",events=None):
    start=rest._stamp(p["config"]["initial_since"])
    return {"id":{"time":rest._iso(when or start+timedelta(hours=1)),
                  "uniqueQualifier":qualifier,"customerId":CUSTOMER,"applicationName":p["config"]["application"]},
            "actor":{"profileId":actor,"email":"secret@example.com"},
            "ipAddress":"192.0.2.1",
            "events":events if events is not None else [{"type":"access","name":"edit",
                  "parameters":[{"name":"doc_id","value":"private-document"},
                                {"name":"doc_title","value":"private-title"}]}]}


def page(items=(),token=None):
    value={"kind":"reports#activities"}
    if items:
        value["items"]=list(items)
    if token is not None:
        value["nextPageToken"]=token
    return httpx.Response(200,json=value)


def factory(handler):
    def make(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return make


def run(p,handler,**kwargs):
    return rest.run(p["profile_id"],p["revision"],confirm_reads=True,confirm_retention=True,
                    client_factory=factory(handler),**kwargs)


def test_native_pagination_projection_deduplication_and_checkpoint():
    p=google.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    one=item(p,when=start+timedelta(hours=23,minutes=30))
    two=item(p,"124",when=start+timedelta(hours=23,minutes=40),
             events=[{"type":"access","name":"view"},{"type":"access","name":"view"}])
    calls=[]
    def fetch(request):
        calls.append(request)
        assert request.url.host=="admin.googleapis.com"
        assert request.url.path=="/admin/reports/v1/activity/users/all/applications/drive"
        assert request.url.params["customerId"]==CUSTOMER
        assert request.headers["Authorization"]=="Bearer "+TOKEN
        assert "parameters(name,value)" in request.url.params["fields"]
        assert "includeSensitiveData" not in request.url.params
        assert "ipAddress" not in request.url.params["fields"] and "email" not in request.url.params["fields"]
        return page([one,two]) if request.url.params.get("pageToken") else page([one],"next")
    result=run(p,fetch)
    assert len(calls)==2 and result["events_added"]==3
    assert result["last_result"]["fallback_case_events"]==2
    assert result["last_result"]["audit_envelopes"]==2
    assert result["last_result"]["duplicate_records_in_window"]==1
    assert result["last_result"]["customer_activity_readback_observed"]
    assert result["provider"]=="google"
    # Same envelope on the next overlapping window, plus new event.
    three=item(p,"125",when=start+timedelta(hours=25))
    second=run(result,lambda _:page([two,one,three]))
    assert second["events_added"]==1 and second["duplicates_skipped"]==3
    assert second["log_id"]==result["log_id"]
    assert workspace.list_logs()["logs"][0]["events"]==4
    with workspace._LOCK:
        c=workspace._connect()
        try:
            raw=str(c.execute("SELECT * FROM events").fetchall())+str(c.execute("SELECT * FROM objects").fetchall())
        finally:
            c.close()
    for secret in ("private-document","private-title","secret@example.com","192.0.2.1","123456789012345",TOKEN):
        assert secret not in raw
    assert "google.drive.access.edit" in raw


@pytest.mark.parametrize("application",google.APPLICATIONS)
def test_all_supported_apps_use_native_endpoint_and_minimal_projection(application):
    p=google.create(config(application=application,case_basis="actor",confirm_drive_parameters=False))
    def fetch(request):
        assert request.url.path.endswith("/applications/"+application)
        assert "parameters" not in request.url.params["fields"]
        assert request.url.params["maxResults"]=="5"
        return page([item(p)])
    result=run(p,fetch)
    assert result["events_added"]==1
    assert result["last_result"]["fallback_case_events"]==0


@pytest.mark.parametrize("overrides",[
    {"confirm_domain_scope":False},{"confirm_audit_retention":False},
    {"confirm_drive_parameters":False},{"customer_id":"my_customer"},
    {"application":"calendar"},{"user_key":"someone@example.com"},
    {"connection_id":"conn_"+"b"*16},
    {"window_hours":1,"overlap_minutes":60},{"page_size":True},
])
def test_configuration_refuses_unapproved_or_unbound_scope(overrides):
    with pytest.raises(rest.FeedError):
        google.create(config(**overrides))


def test_scope_and_connection_inspection_is_passive(setup,monkeypatch):
    def no_network(*args):
        pytest.fail("passive inventory must not access bearer")
    monkeypatch.setattr(oauth,"bearer_for",no_network)
    p=google.create(config())
    assert rest.inventory()["profiles"][0]["credential_binding_available"]
    r=ops.inventory()
    provider=next(x for x in r["providers"] if x["provider"]=="google")
    assert provider["stored_oauth_connections"]==1
    assert next(x for x in r["profiles"] if x["target_id"]==p["profile_id"])["provider"]=="google"
    assert CUSTOMER not in json.dumps(r) and CONNECTION not in json.dumps(r)
    setup[0]["scopes"]=["https://www.googleapis.com/auth/drive"]
    assert not rest.inventory()["profiles"][0]["credential_binding_available"]
    with pytest.raises(rest.FeedError,match="google_grant_scope_mismatch"):
        run(p,lambda _:pytest.fail("wrong scope must prevent HTTP"))


@pytest.mark.parametrize("field,value",[
    ("customerId","C-other"),("applicationName","gmail"),("uniqueQualifier","invalid"),
])
def test_cross_tenant_application_and_bad_identity_no_checkpoint(field,value):
    p=google.create(config())
    bad=item(p);bad["id"][field]=value
    with pytest.raises(rest.FeedError):
        run(p,lambda _:page([bad]))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_exact_selected_actor_not_substituted():
    p=google.create(config(user_key="123456789012345",case_basis="actor",confirm_domain_scope=False))
    def fetch(request):
        assert "/users/123456789012345/" in request.url.path
        return page([item(p,actor="999999999")])
    with pytest.raises(rest.FeedError,match="google_selected_actor_mismatch"):
        run(p,fetch)


def test_order_independent_subevent_projection_and_conflicting_replay():
    p=google.create(config())
    start=rest._stamp(p["config"]["initial_since"])
    one=item(p,when=start+timedelta(hours=23,minutes=50),
             events=[{"type":"access","name":"edit"},{"type":"access","name":"view"}])
    first=run(p,lambda _:page([one]))
    reversed_item={**one,"events":list(reversed(one["events"]))}
    second=run(first,lambda _:page([reversed_item]))
    assert second["events_added"]==0 and second["duplicates_skipped"]==2


def test_late_arrival_overlap_and_empty_window():
    p=google.create(config())
    result=run(p,lambda _:page())
    assert result["log_id"] is None
    assert result["checkpoint"] is not None
    assert not result["last_result"]["customer_activity_readback_observed"]


def test_page_failure_and_cursor_loop_leave_no_partial_data():
    p=google.create(config())
    with pytest.raises(rest.FeedError,match="invalid_or_repeated_provider_cursor"):
        run(p,lambda _:page([item(p)],"same"))
    assert rest.inventory()["profiles"][0]["checkpoint"] is None
    assert workspace.list_logs()["logs"]==[]


def test_google_report_age_guard_never_silently_skips_history():
    p=google.create(config())
    with pytest.raises(rest.FeedError,match="google_checkpoint_older_than_provider_history"):
        run(p,lambda _:pytest.fail("aged checkpoint must not read"),now=rest._now()+timedelta(days=181))


def test_revocation_during_http_invalidates_commit(setup):
    p=google.create(config())
    def fetch(_):
        setup.clear()
        return page([item(p)])
    with pytest.raises(rest.FeedError,match="google_grant_unavailable"):
        run(p,fetch)
    assert workspace.list_logs()["logs"]==[]
    assert rest.inventory()["profiles"][0]["checkpoint"] is None


def test_recurring_google_profile_uses_shared_scheduler_and_stale_grant_pause(setup,monkeypatch):
    p=google.create(config())
    schedule=scheduler.create("enterprise_rest_feed",p["profile_id"],interval_minutes=15,
        confirm_scheduled_runs=True,confirm_rest_read_retention=True,expected_rest_profile_revision=1)
    setup.clear()
    monkeypatch.setattr(scheduler,"_recording",lambda:True)
    result=scheduler.run_due(now=rest.time.time()+1000)
    assert result[0]["outcome"]=="paused:rest_profile_changed"
    assert not scheduler.status()["schedules"][0]["enabled"]


def test_google_pkce_offline_scope_and_renewal(tmp_path,monkeypatch):
    monkeypatch.setattr(oauth,"auth_dir",lambda:tmp_path/"oauth")
    monkeypatch.setattr(oauth,"_read_or_create_secret",lambda _: "local-fixture")
    oauth._pending.clear();oauth._access.clear()
    calls=[]
    def transport(request):
        calls.append(request)
        form=parse_qs(request.content.decode())
        if request.url.path=="/revoke":
            assert set(form)=={"token"}
            return httpx.Response(200)
        if form.get("grant_type")==["refresh_token"]:
            return httpx.Response(200,json={"access_token":"new-access","expires_in":3600,"scope":google.SCOPE})
        return httpx.Response(200,json={"access_token":"initial-access","refresh_token":"refresh-secret","expires_in":3600,"scope":google.SCOPE})
    monkeypatch.setattr(oauth,"_transport",httpx.MockTransport(transport))
    started=oauth.begin("google",client_id="desktop-client")
    url=urlsplit(started["authorization_url"]);query=parse_qs(url.query)
    assert url.netloc=="accounts.google.com" and query["access_type"]==["offline"]
    assert query["scope"]==[google.SCOPE] and query["code_challenge_method"]==["S256"]
    conn=oauth.complete(query["state"][0],"code")
    oauth._access.clear()
    assert oauth.bearer_for(conn["connection_id"],"google")=="new-access"
    assert oauth.disconnect(conn["connection_id"])["provider_revocation"]=="revoked"
    with pytest.raises(oauth.OAuthError):
        oauth.begin("google",client_id="desktop-client",scopes=["https://www.googleapis.com/auth/drive"])
