"""Managed credential delivery, identity-verified rebind and continuity tests."""
import json
import os

import httpx
import pytest

from server import enterprise_connector_secrets as secrets
from server import enterprise_connector_rotation as rotation
from server import enterprise_rest_feed as rest
from server import event_log_workspace as ws
from server import hubspot_history_feed as hs
from server import dataverse_audit_feed as dv
from gateway.connector_jobs import Rebind, Change, Claim, JobError, profile_digest
from gateway.connector_event_publications import Remove
from test_connector_event_publications import deployed, source, grant, upload
from test_gateway_connector_jobs import db

REF="OWG_REST_TOKEN_TEST"
PRIVATE="fixture-private-secret"


@pytest.fixture
def file_source(tmp_path,monkeypatch):
    root=tmp_path.resolve()/"secrets"
    root.mkdir(mode=0o700)
    file=root/"credential"
    file.write_text(PRIVATE+"\n")
    file.chmod(0o600)
    monkeypatch.setenv(secrets.ROOT,str(root))
    monkeypatch.setenv(secrets.CATALOG,json.dumps({REF:"credential"}))
    monkeypatch.setenv(REF,"stale-environment-secret")
    return root,file


@pytest.mark.skipif(os.name!="posix",reason="POSIX secret delivery")
def test_private_file_is_authoritative_and_atomic_replacement(file_source):
    root,file=file_source
    assert secrets.resolve(REF)==PRIVATE
    assert secrets.delivery(REF)=="private_file"
    replacement=root/"replacement"
    replacement.write_text("replacement-secret\r\n")
    replacement.chmod(0o600)
    replacement.replace(file)
    assert secrets.resolve(REF)=="replacement-secret"
    file.unlink()
    with pytest.raises(secrets.SecretError,match="unavailable"):
        secrets.resolve(REF)


@pytest.mark.skipif(os.name!="posix",reason="POSIX secret delivery")
@pytest.mark.parametrize("kind",["permissions","symlink","hardlink","directory","fifo","oversized","multiline","invalid_utf8"])
def test_invalid_file_never_falls_back_to_environment(file_source,kind):
    root,file=file_source
    if kind=="permissions":
        file.chmod(0o644)
    elif kind=="symlink":
        real=root/"other";file.rename(real);file.symlink_to(real)
    elif kind=="hardlink":
        os.link(file,root/"other")
    elif kind=="directory":
        file.unlink();file.mkdir(mode=0o700)
    elif kind=="fifo":
        file.unlink();os.mkfifo(file,0o600)
    elif kind=="oversized":
        file.write_bytes(b"x"*(secrets.MAX_BYTES+1))
    elif kind=="multiline":
        file.write_text("first\nsecond\n")
    else:
        file.write_bytes(b"\xff")
    with pytest.raises(secrets.SecretError):
        secrets.resolve(REF)


@pytest.mark.skipif(os.name!="posix",reason="POSIX secret delivery")
def test_root_must_be_private_and_not_symlinked(file_source,monkeypatch):
    root,file=file_source
    root.chmod(0o755)
    with pytest.raises(secrets.SecretError,match="owner_only"):
        secrets.resolve(REF)
    root.chmod(0o700)
    link=root.parent/"link"
    link.symlink_to(root,target_is_directory=True)
    monkeypatch.setenv(secrets.ROOT,str(link))
    with pytest.raises(secrets.SecretError):
        secrets.resolve(REF)


@pytest.mark.parametrize("catalog",[
    '{"OWG_REST_TOKEN_TEST":"../escape"}',
    '{"OWG_REST_TOKEN_TEST":"a","OWG_REST_TOKEN_TEST":"b"}',
    '{"OWG_REST_TOKEN_TEST":"a","OWG_SLACK_TOKEN_TEST":"a"}',
    '{"PATH":"a"}','[]',
])
def test_bad_catalog_refused(catalog,monkeypatch):
    monkeypatch.setenv(secrets.CATALOG,catalog)
    monkeypatch.setenv(REF,PRIVATE)
    with pytest.raises(secrets.SecretError,match="invalid_connector_secret_catalog"):
        secrets.resolve(REF)


def test_environment_backwards_compatible(monkeypatch):
    monkeypatch.delenv(secrets.CATALOG,raising=False)
    monkeypatch.setenv(REF,PRIVATE)
    assert secrets.resolve(REF)==PRIVATE
    assert secrets.delivery(REF)=="environment"
    with pytest.raises(secrets.SecretError):
        secrets.resolve("PATH")


@pytest.fixture
def hubspot_profile(tmp_path,monkeypatch):
    from test_hubspot_history_feed import config,record,run,batch,ENV,TOKEN,SCOPES
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path.resolve())
    monkeypatch.delenv(secrets.CATALOG,raising=False)
    monkeypatch.setenv(ENV,TOKEN)
    monkeypatch.setenv(hs.CATALOG,json.dumps({"primary":{"hub_id":1001,"app_id":2001,
                           "credential_env":ENV,"scopes":SCOPES}}))
    p=hs.create(config(selections=[{"object_type":"contacts","record_ids":["100"],
                                      "properties":["status","owner"]}]))
    retained=run(p,lambda obj,body:batch([record(p,obj)]))
    disabled=rest.change(p["profile_id"],retained["revision"],False)
    monkeypatch.setenv(ENV,"pat-na1-rotated-fixture")
    return disabled,ENV,SCOPES


def spec(p,**overrides):
    return {"revision":p["revision"],"confirm_rotation":True,"confirm_identity_probe":True,
            "confirm_workers_stopped":True,**overrides}


def factory(handler):
    def make(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return httpx.Client(transport=httpx.MockTransport(handler),**kwargs)
    return make


def private(p):
    with ws._LOCK:
        db=rest._connect()
        try:
            return rest._get(db,p["profile_id"])
        finally:
            db.close()


def test_hubspot_rotation_preserves_log_checkpoint_and_key(hubspot_profile):
    p,env,scopes=hubspot_profile
    old=private(p);calls=[]
    def handler(req):
        calls.append(req.url.path)
        assert req.url.path==hs.IDENTITY
        assert req.headers["Authorization"]=="Bearer pat-na1-rotated-fixture"
        return httpx.Response(200,json={"hubId":1001,"appId":2001,"scopes":scopes})
    result=rotation.rotate(p["profile_id"],spec(p),client_factory=factory(handler))
    new=private(p)
    assert calls==[hs.IDENTITY]
    assert new["key"]==old["key"] and new["credential_binding"]!=old["credential_binding"]
    assert result["revision"]==p["revision"]+1 and not result["enabled"]
    assert result["checkpoint"]==p["checkpoint"] and result["log_id"]==p["log_id"]
    assert result["credential_binding_available"]
    assert result["credential_management"]["last_rotation"]["identity_readback_verified"]
    assert "pat-na1-rotated-fixture" not in json.dumps(result)
    with pytest.raises(rest.FeedError,match="profile_changed"):
        rest.change(p["profile_id"],p["revision"],True)
    enabled=rest.change(p["profile_id"],result["revision"],True)
    assert enabled["enabled"] and enabled["revision"]==result["revision"]+1


def test_wrong_account_and_secret_race_leave_old_binding(hubspot_profile,monkeypatch):
    p,env,scopes=hubspot_profile;old=private(p)
    with pytest.raises(rest.FeedError,match="hubspot_account_app_or_scope_mismatch"):
        rotation.rotate(p["profile_id"],spec(p),client_factory=factory(lambda _:httpx.Response(200,
            json={"hubId":999,"appId":2001,"scopes":scopes})))
    assert private(p)==old
    def changed(_):
        monkeypatch.setenv(env,"pat-na1-replaced-again")
        return httpx.Response(200,json={"hubId":1001,"appId":2001,"scopes":scopes})
    with pytest.raises(rest.FeedError,match="credential_changed_during_rotation"):
        rotation.rotate(p["profile_id"],spec(p),client_factory=factory(changed))
    assert private(p)==old


def test_rotation_requires_explicit_review(hubspot_profile):
    p,_,_=hubspot_profile
    for field in ("confirm_rotation","confirm_identity_probe","confirm_workers_stopped"):
        with pytest.raises(rest.FeedError,match="explicit_rotation"):
            rotation.rotate(p["profile_id"],spec(p,**{field:False}),
                            client_factory=factory(lambda _:pytest.fail("no identity request")))


def test_dataverse_rotation_only_token_and_whoami(tmp_path,monkeypatch):
    from test_dataverse_audit_feed import config,gid,ENV,SECRET,ORIGIN,TOKEN
    monkeypatch.setattr(ws,"DATA_DIR",tmp_path.resolve())
    monkeypatch.delenv(secrets.CATALOG,raising=False)
    monkeypatch.setenv(ENV,SECRET)
    monkeypatch.setenv(dv.CATALOG,json.dumps({"primary":{"origin":ORIGIN,"tenant_id":gid(1),
        "client_id":gid(2),"organization_id":gid(3),"application_user_id":gid(4),"credential_env":ENV}}))
    p=dv.create(config())
    p=rest.change(p["profile_id"],p["revision"],False)
    monkeypatch.setenv(ENV,"replacement-application-secret")
    calls=[]
    def handler(req):
        calls.append(req.url.path)
        if req.url.host=="login.microsoftonline.com":
            assert b"replacement-application-secret" in req.content
            return httpx.Response(200,json={"access_token":TOKEN,"token_type":"Bearer","expires_in":3600})
        assert req.url.path.endswith("/WhoAmI")
        assert req.headers["Authorization"]=="Bearer "+TOKEN
        return httpx.Response(200,json={"OrganizationId":gid(3),"UserId":gid(4)})
    result=rotation.rotate(p["profile_id"],spec(p),client_factory=factory(handler))
    assert len(calls)==2 and not result["enabled"] and result["credential_binding_available"]
    assert result["checkpoint"] is None


def rebind_spec(j,**overrides):
    return Rebind.model_validate({"expected_revision":j["revision"],"profile_id":j["profile_id"],
           "old_profile_revision":j["profile_revision"],"old_profile_sha256":j["profile_sha256"],
           "new_profile_revision":j["profile_revision"]+2,"new_profile_sha256":"b"*64,
           "confirm_same_source":True,"confirm_workers_reconciled":True,
           "confirm_dispatch_and_receipts":True,**overrides})


def test_gateway_rebind_requires_paused_job_and_revokes_worker(deployed):
    jobs,store=deployed
    j,old,snap,_=source(deployed)
    org=j["organization_id"];job=j["job_id"]
    with pytest.raises(JobError,match="pause_job"):
        jobs.rebind(org,job,rebind_spec(j),"owner")
    paused=jobs.change(org,job,Change(expected_revision=j["revision"],enabled=False,confirm_change=True),"owner")
    with pytest.raises(JobError,match="explicit_profile_rebind"):
        jobs.rebind(org,job,rebind_spec(paused,confirm_workers_reconciled=False),"owner")
    result=jobs.rebind(org,job,rebind_spec(paused),"owner")
    rebound=result["job"];new=result["worker_token"]
    assert rebound["job_id"]==job and rebound["profile_id"]==j["profile_id"]
    assert not rebound["enabled"] and rebound["profile_revision"]==j["profile_revision"]+2
    with pytest.raises(JobError,match="worker_credential_required"):
        jobs.claim(old,Claim(profile_id=j["profile_id"],profile_revision=j["profile_revision"],profile_sha256=j["profile_sha256"]))
    with pytest.raises(JobError,match="worker_profile_pin_mismatch"):
        jobs.claim(new,Claim(profile_id=j["profile_id"],profile_revision=j["profile_revision"],profile_sha256=j["profile_sha256"]))
    claim=jobs.claim(new,Claim(profile_id=j["profile_id"],profile_revision=rebound["profile_revision"],profile_sha256=rebound["profile_sha256"]))
    assert not claim["claimed"]


def test_existing_publication_blocks_rebind(deployed):
    jobs,store=deployed
    j,token,snap,_=source(deployed)
    g=grant(store,j,snap);upload(store,token,g,snap)
    org=j["organization_id"];job=j["job_id"]
    paused=jobs.change(org,job,Change(expected_revision=j["revision"],enabled=False,confirm_change=True),"owner")
    with pytest.raises(JobError,match="delete_prior_publication"):
        jobs.rebind(org,job,rebind_spec(paused),"owner")
    store.remove(org,job,Remove(expected_publication_revision=g["revision"],confirm_delete=True),"owner",g["grant_id"])
    assert jobs.rebind(org,job,rebind_spec(paused),"owner")["shown_once"]
