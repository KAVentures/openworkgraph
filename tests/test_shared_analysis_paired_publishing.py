"""R4: atomic shared aggregate + separately permissioned coded-case publication."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.analysis_case_data import Attach, CaseData, READ, WRITE
from gateway.analysis_paired_publishing import PairedPublish, publish
from gateway.analysis_publications import Access, PublicationError
from tests.test_shared_analysis_publications import app as app_factory
from tests.test_shared_analysis_cases import p, dataset, parent_store, process_store, person, publication, snapshot


def packet(**changes):
    data = {
        "title": "Reviewed aggregate and coded cases",
        "snapshot": snapshot(), "case_dataset": dataset(),
        "expected_revision": 0, "expires_in_days": 7,
        "user_confirmed": True, "organization_sharing_confirmed": True,
        "case_sharing_confirmed": True,
    }
    return PairedPublish.model_validate({**data, **changes})


def _tables(store):
    with store.db.connect() as c:
        return {table: int(store.q(c, f"SELECT count(*) FROM {table} WHERE organization_id=?", ("acme",)).fetchone()[0])
                for table in ("gateway_analysis_views", "gateway_analysis_revisions",
                              "gateway_analysis_case_data", "gateway_analysis_case_grants")}


def test_one_transaction_publishes_both_and_defaults_to_no_case_viewers(parent_store):
    result = publish(parent_store, p(), packet())
    pub, child = result["publication"], result["case_data"]
    assert result["atomic_publication"] is True
    assert result["case_viewer_grants_created"] is False
    assert result["retained_source_log_uploaded"] is False
    assert result["execution_authorized"] is False
    assert pub["revision"] == child["revision"] == 1
    assert pub["access_revision"] == 1 and child["attached"] is True
    assert pub["title"] == "Reviewed aggregate and coded cases"
    assert _tables(parent_store) == {
        "gateway_analysis_views": 1,
        "gateway_analysis_revisions": 1,
        "gateway_analysis_case_data": 1,
        "gateway_analysis_case_grants": 0,
    }
    owner=CaseData(parent_store)
    assert owner.read(p(), pub["view_id"], 1)["matching_cases"] == 4
    parent_store.access(p(), pub["view_id"], Access(expected_access_revision=1,members=[
        {"actor_id":"bob", "role":"viewer"}],user_confirmed=True))
    assert parent_store.detail(person("bob"),pub["view_id"])["revision"] == 1
    with pytest.raises(PublicationError):
        owner.read(p("bob"),pub["view_id"],1)
    audit=json.dumps(parent_store.db.audit_rows("acme",50))
    assert "analysis.published" in audit
    assert "analysis.cases.published" in audit
    assert "analysis.paired_published" in audit
    assert "elapsed_microseconds" not in audit
    assert "C1" not in audit


def test_atomic_rollback_after_both_rows_and_audit_were_written(parent_store,monkeypatch):
    original = CaseData.attach_in

    def fail_after_insert(self,c,principal,view_id,body):
        original(self,c,principal,view_id,body)
        raise RuntimeError("synthetic post-case-attachment failure")

    monkeypatch.setattr(CaseData,"attach_in",fail_after_insert)
    with pytest.raises(RuntimeError,match="post-case-attachment failure"):
        publish(parent_store,p(),packet())
    assert _tables(parent_store) == {
        "gateway_analysis_views": 0,
        "gateway_analysis_revisions": 0,
        "gateway_analysis_case_data": 0,
        "gateway_analysis_case_grants": 0,
    }
    audit=json.dumps(parent_store.db.audit_rows("acme",50))
    assert "analysis.published" not in audit
    assert "analysis.cases.published" not in audit
    assert "analysis.paired_published" not in audit


def test_case_capacity_failure_does_not_leave_orphan_aggregate(parent_store,monkeypatch):
    import gateway.analysis_case_data as cases_module
    monkeypatch.setattr(cases_module,"MAX_ATTACHMENTS",0)
    with pytest.raises(PublicationError,match="capacity"):
        publish(parent_store,p(),packet())
    assert _tables(parent_store)["gateway_analysis_views"] == 0
    assert _tables(parent_store)["gateway_analysis_revisions"] == 0


def test_mismatched_fingerprints_or_population_and_lack_of_confirmation_refuse_before_write(parent_store):
    for body in [
        packet(case_dataset=dataset().model_copy(update={"source_content_sha256":"f"*64})),
        packet(case_dataset=dataset().model_copy(update={"analysis_spec_sha256":"e"*64})),
        packet(case_dataset=dataset().model_copy(update={"cases":dataset().cases[:3]})),
        packet(user_confirmed=False),
        packet(organization_sharing_confirmed=False),
        packet(case_sharing_confirmed=False),
    ]:
        with pytest.raises(PublicationError):
            publish(parent_store,p(),body)
    assert _tables(parent_store)["gateway_analysis_views"] == 0


def test_successor_revision_is_atomic_owner_only_and_cases_have_no_inherited_grants(parent_store):
    from gateway.analysis_publications import Access
    store = parent_store
    first = publish(store, p(), packet())["publication"]
    wid = first["view_id"]
    store.access(p(), wid, Access(expected_access_revision=1, members=[
        {"actor_id":"bob","role":"editor"}],user_confirmed=True))
    # The prior aggregate grant survives, but no case grant is inferred.
    assert store.detail(p("bob"),wid)["viewing_revision"] == 1
    with pytest.raises(PublicationError):
        CaseData(store).read(p("bob"),wid,1)
    request = packet(title="Updated review", expected_revision=1,
                     expected_parent_access_revision=2)
    with pytest.raises(PublicationError):
        publish(store, p("bob"), request, wid)
    with pytest.raises(PublicationError):
        publish(store, p("root",extra={"org:analysis:admin"}), request, wid)
    with pytest.raises(PublicationError, match="revision or access"):
        publish(store, p(), packet(expected_revision=1,
                                  expected_parent_access_revision=1), wid)
    second=publish(store,p(),request,wid)
    assert second["publication"]["view_id"] == wid
    assert second["publication"]["revision"] == 2
    assert second["case_data"]["revision"] == 2
    assert _tables(store)["gateway_analysis_case_grants"] == 0
    assert CaseData(store).read(p(),wid,1)["matching_cases"] == 4
    assert CaseData(store).read(p(),wid,2)["matching_cases"] == 4
    assert store.detail(p("bob"),wid,1)["title"] == first["title"]
    assert store.detail(p("bob"),wid,2)["title"] == "Updated review"
    with pytest.raises(PublicationError):
        CaseData(store).read(p("bob"),wid,2)
    with pytest.raises(PublicationError, match="revision or access"):
        publish(store,p(),request,wid)
    assert _tables(store)["gateway_analysis_revisions"] == 2
    assert _tables(store)["gateway_analysis_case_data"] == 2


def test_successor_second_phase_failure_keeps_old_revision_and_grants(parent_store,monkeypatch):
    from gateway.analysis_publications import Access
    first=publish(parent_store,p(),packet())["publication"]
    wid=first["view_id"]
    parent_store.access(p(),wid,Access(expected_access_revision=1,members=[
        {"actor_id":"bob","role":"viewer"}],user_confirmed=True))
    before=_tables(parent_store)
    audit_before=json.dumps(parent_store.db.audit_rows("acme",100))
    original=CaseData.attach_in
    def fail(self,conn,principal,view_id,body):
        original(self,conn,principal,view_id,body)
        raise RuntimeError("Injected successor abort")
    monkeypatch.setattr(CaseData,"attach_in",fail)
    with pytest.raises(RuntimeError,match="Injected successor abort"):
        publish(parent_store,p(),packet(expected_revision=1,
               expected_parent_access_revision=2),wid)
    assert _tables(parent_store)==before
    assert parent_store.detail(p(),wid)["revision"]==1
    assert CaseData(parent_store).read(p(),wid,1)["matching_cases"]==4
    with pytest.raises(PublicationError):
        parent_store.detail(p(),wid,2)
    # Read-only verification is intentionally audited, so compare only the
    # publication/write records. The aborted transaction must add NO durable
    # publication, case attachment or paired-publication audit entry.
    audit_after = parent_store.db.audit_rows("acme",100)
    write_actions = {"analysis.published", "analysis.cases.published",
                     "analysis.paired_published", "analysis.access_changed"}
    before_writes = [row for row in json.loads(audit_before)
                     if row["action"] in write_actions]
    after_writes = [row for row in audit_after
                    if row["action"] in write_actions]
    assert after_writes == before_writes


def test_all_four_scopes_required_and_old_single_publication_path_unchanged(parent_store):
    for who in [person(),
                person("bob",scopes=frozenset({"org:analysis:read",READ,WRITE})),
                person("foreign",org="foreign",scopes=frozenset({"org:analysis:read","org:analysis:write",READ}))]:
        with pytest.raises(PublicationError):
            publish(parent_store,who,packet())
    assert _tables(parent_store)["gateway_analysis_views"] == 0
    old = parent_store.publish(p(),publication())
    assert parent_store.detail(p(),old["view_id"])["revision"] == 1
    assert CaseData(parent_store).attach(
        p(),old["view_id"],
        Attach(
            revision=1,parent_content_sha256=old["content_sha256"],
            expected_parent_access_revision=1,dataset=dataset(),
            user_confirmed=True,case_sharing_confirmed=True
        )
    )["attached"]


def test_exact_contract_refuses_extra_or_invalid_values():
    doc=packet().model_dump(mode="json")
    for bad in [{**doc,"expected_revision":-1},{**doc,"expected_revision":10},
                {**doc,"expected_parent_access_revision":0},{**doc,"unknown":True},
                {**doc,"case_sharing_confirmed":1},{**doc,"expires_in_days":0}]:
        with pytest.raises(ValidationError):
            PairedPublish.model_validate(bad)


def test_http_one_request_and_access_separation(tmp_path):
    app=app_factory(tmp_path)
    app.state.human_access_settings.group_scope_map["analysis"]|={READ,WRITE}
    h=lambda who:{"Authorization":"Bearer "+who}
    path="/v1/human/analyses/with-cases"
    body=packet().model_dump(mode="json")
    with TestClient(app) as c:
        assert c.post(path,headers=h("read-only"),content=b"not json").status_code==403
        assert c.post(path,headers=h("process-only"),json=body).status_code==403
        for h2 in [h("bad"),{"Authorization":"OWG-Process-Session fake"}]:
            assert c.post(path,headers=h2,json=body).status_code==401
        assert c.post(path,headers=h("alice"),content=b"{}",).status_code==415
        assert c.post(path,headers={**h("alice"),"Content-Type":"application/json"},
                      content=b'{"title":"a","title":"b"}').status_code==422
        assert c.post(path,headers={**h("alice"),"Content-Type":"application/json"},
                      content=b"x"*(721*1024)).status_code==413
        made=c.post(path,headers=h("alice"),json=body)
        assert made.status_code==200,made.text
        assert made.headers["cache-control"]=="no-store"
        value=made.json()
        assert value["atomic_publication"] and value["case_viewer_grants_created"] is False
        parent=value["publication"]
        url="/v1/human/analyses/"+parent["view_id"]
        assert c.get(url,headers=h("bob")).status_code==404
        assert c.get(url+"/case-data?revision=1",headers=h("bob")).status_code==404
        assert c.get(url+"/case-data?revision=1",headers=h("alice")).status_code==200
        successor={**body,"expected_revision":1,"expected_parent_access_revision":1,
                   "title":"Updated operational cases"}
        assert c.post(url+"/with-cases",headers=h("bob"),
                      content=b"not JSON").status_code in (403,404)
        assert c.post(url+"/with-cases",headers=h("alice"),
                      json={**successor,"expected_revision":0}).status_code==422
        updated=c.post(url+"/with-cases",headers=h("alice"),json=successor)
        assert updated.status_code==200,updated.text
        assert updated.json()["publication"]["revision"]==2
        assert c.get(url+"?revision=1",headers=h("alice")).json()["title"]==body["title"]
        assert c.get(url+"?revision=2",headers=h("alice")).json()["title"]=="Updated operational cases"
        assert c.get(url+"/case-data?revision=2",headers=h("alice")).status_code==200
        assert c.get(url+"/case-data?revision=2",headers=h("bob")).status_code==404
        assert c.post(url+"/with-cases",headers=h("alice"),
                      json=successor).status_code==409
        assert c.post(path,headers=h("alice"),
                      json={**body,"case_dataset":{**body["case_dataset"],"cases":body["case_dataset"]["cases"][:3]}}).status_code==422
        views=c.get("/v1/human/analyses",headers=h("alice")).json()["views"]
        assert len(views)==1


def test_browser_has_bounded_separate_case_review_and_no_auto_case_grants():
    from pathlib import Path
    ui=(Path(__file__).resolve().parents[1]/"gateway"/"analyses.html").read_text(encoding="utf-8")
    for string in ["publishPaired","pairedCaseConfirm","pairedCasePreview",
                   "window.owgAnalysisCases.validate", "case_sharing_confirmed:true",
                   "expected_revision:selected?.revision||0","/with-cases","pairedCaseFileAttempt"]:
        assert string in ui
    assert "innerHTML" not in ui
    assert "localStorage" not in ui
    assert "sessionStorage" not in ui
