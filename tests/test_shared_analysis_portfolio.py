"""All-or-nothing authorized cross-publication aggregate comparison."""
from datetime import datetime,timedelta,timezone
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from gateway.analysis_portfolio import PortfolioRequest,review
from gateway.analysis_publications import Access,PublicationError
from test_shared_analysis_publications import store,process_store,person,app as app_factory,snapshot as grouped
from test_shared_analysis_revision_comparisons import published


def pin(detail):
    return {"view_id":detail["view_id"],"revision":detail["viewing_revision"],
            "expected_content_sha256":detail["content_sha256"]}


def select(*details,baseline=0):
    return PortfolioRequest(selections=[pin(d) for d in details],baseline_index=baseline)


def test_three_publications_and_nonfirst_baseline(store):
    a=store.publish(person(),published(4,source="a"))
    b=store.publish(person(),published(8,source="c"))
    c=store.publish(person(),published(2,source="d"))
    r=review(store,person(),select(a,b,c,baseline=1))
    assert r["comparable_count"]==2 and r["incomparable_count"]==0
    assert [x["changes"][0]["change_decimal"] for x in r["comparisons"]]==["-4","-6"]
    assert r["baseline_index"]==1
    assert r["report_sha256"]==review(store,person(),select(a,b,c,baseline=1))["report_sha256"]
    assert not any(r[k] for k in ("source_independently_verified","case_identities_joined",
                                   "pooled_totals_reported","ranking_reported","execution_authorized"))
    audit=json.dumps(store.db.audit_rows("acme",50))
    assert "analysis.portfolio_compared" in audit
    assert "Synthetic case counts" not in audit
    assert "change_decimal" not in audit


def test_refuses_incompatible_pairs_individually_without_group_codes(store):
    a=store.publish(person(),published(4))
    b=store.publish(person(),published(8,source="c"))
    c=store.publish(person(),published(snap=grouped(6)))
    r=review(store,person(),select(a,b,c))
    assert r["comparable_count"]==1 and r["incomparable_count"]==1
    assert r["comparisons"][1]["changes"]==[]
    assert "group_codes_have_no_cross_export_identity" in r["comparisons"][1]["refusal_reasons"]
    assert "G1_1" not in json.dumps(r)


def test_each_publication_acl_and_revocation_rechecked(store):
    a=store.publish(person(),published(4))
    b=store.publish(person(),published(8,source="c"))
    def grant(detail):
        store.access(person(),detail["view_id"],Access(expected_access_revision=1,
             members=[{"actor_id":"bob","role":"viewer"}],user_confirmed=True))
    grant(a)
    request=select(a,b)
    reader=person("bob",scopes={"org:analysis:read"})
    with pytest.raises(PublicationError) as exc:review(store,reader,request)
    assert exc.value.status==404
    assert not any(x["action"]=="analysis.portfolio_compared" for x in store.db.audit_rows("acme",50))
    grant(b)
    assert review(store,reader,request)["comparable_count"]==1
    store.access(person(),b["view_id"],Access(expected_access_revision=2,members=[],user_confirmed=True))
    with pytest.raises(PublicationError) as exc:review(store,reader,request)
    assert exc.value.status==404
    with pytest.raises(PublicationError) as exc:review(store,person("eve",org="foreign"),request)
    assert exc.value.status==404


def test_stale_digest_and_corrupt_revision_fail_without_partial_report(store):
    a=store.publish(person(),published(4))
    b=store.publish(person(),published(8,source="c"))
    request=select(a,b)
    bad=request.model_dump()
    bad["selections"][1]["expected_content_sha256"]="0"*64
    with pytest.raises(PublicationError) as exc:review(store,person(),PortfolioRequest.model_validate(bad))
    assert exc.value.status==409
    with store.db.connect() as conn:
        store.q(conn,"UPDATE gateway_analysis_revisions SET content_sha256=? WHERE organization_id=? AND view_id=? AND revision=?",
                ("0"*64,"acme",b["view_id"],1))
    with pytest.raises(PublicationError,match="integrity"):review(store,person(),request)
    assert not any(x["action"]=="analysis.portfolio_compared" for x in store.db.audit_rows("acme",50))


def test_exact_old_revision_is_not_silently_replaced(store):
    a=store.publish(person(),published(4))
    b=store.publish(person(),published(8,source="c"))
    store.publish(person(),published(12,expected=1,source="d"),b["view_id"])
    r=review(store,person(),select(a,b))
    assert r["selections"][1]["revision"]==1
    assert r["comparisons"][0]["changes"][0]["after"]==8


def test_validation_and_bounds():
    x={"view_id":"ana_"+"a"*32,"revision":1,"expected_content_sha256":"b"*64}
    y={**x,"view_id":"ana_"+"c"*32}
    for value in ({"selections":[x]},{"selections":[x,x]},{"selections":[x,y],"baseline_index":2},
                  {"selections":[x,{**y,"revision":True}]},{"selections":[x,y],"extra":1},
                  {"selections":[{**x,"revision":11},y]}):
        with pytest.raises(ValidationError):PortfolioRequest.model_validate(value)


def test_http_authentication_authorization_and_strict_body(tmp_path):
    h=lambda who:{"Authorization":"Bearer "+who}
    with TestClient(app_factory(tmp_path)) as client:
        base="/v1/human/analyses"
        a=client.post(base,headers=h("alice"),json=published().model_dump(mode="json"))
        b=client.post(base,headers=h("alice"),json=published(8,source="c").model_dump(mode="json"))
        assert a.status_code==b.status_code==200
        request=select(a.json(),b.json()).model_dump(mode="json")
        path="/v1/human/analysis-portfolio/compare"
        for who,expected in [("read-only",404),("process-only",403),("bob",404),("foreign",404),("bad",401)]:
            assert client.post(path,headers=h(who),json=request).status_code==expected
        response=client.post(path,headers=h("alice"),json=request)
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="no-store"
        assert response.json()["comparisons"][0]["changes"][0]["change_decimal"]=="4"
        assert client.post(path,headers=h("alice"),json={**request,"extra":True}).status_code==422
        assert client.post(path,headers={**h("alice"),"Content-Type":"application/json"},
                           content=b"{}"+b" "*9000).status_code==413
