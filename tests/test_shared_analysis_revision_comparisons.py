"""R4 company-shared revision diffs with exact ACL, digest and no coded-group joins."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest
from fastapi.testclient import TestClient

from gateway.analysis_revision_comparisons import compare, compare_snapshots
from gateway.analysis_publications import Access, Publish, PublicationError
from shared.analysis_snapshot import Snapshot
from test_shared_analysis_publications import (
    store, process_store, person, app as app_factory, snapshot as grouped,
)


def plain(value=4, *, source="a", spec="b", coverage=None):
    return Snapshot(
        generated_at=datetime(2026,1,1,tzinfo=timezone.utc), level="cases",
        columns=[{"heading":"Measure 1","kind":"number","aggregation":"count","unit":"cases"}],
        rows=[[value]], matching_cases=value if value >= 0 else 0,
        matching_rows=value if value >= 0 else 0, rows_truncated=False,
        source_content_sha256=source*64, analysis_spec_sha256=spec*64,
        **(coverage or {}),
    )


def published(value=4, *, expected=0, source="a", spec="b", snap=None):
    return Publish(
        title="Synthetic case counts",
        snapshot=snap or plain(value,source=source,spec=spec),
        expected_revision=expected, user_confirmed=True,
        organization_sharing_confirmed=True,
    )


def two(store, left=4, right=6, *, right_source="c"):
    original=store.publish(person(),published(left))
    wid=original["view_id"]
    store.publish(person(),published(right,expected=1,source=right_source),wid)
    return wid


def test_numeric_count_changes_are_decimal_and_never_causal(store):
    wid=two(store)
    r=compare(store,person(),wid,1,2)
    assert r["comparable"] and not r["refusal_reasons"]
    assert r["view_id"]==wid
    assert r["format"]=="openworkgraph.analysis-revision-comparison.v1"
    assert not r["same_claimed_source"] and r["same_claimed_analysis_spec"]
    assert r["left"]["revision"]==1 and r["right"]["revision"]==2
    assert r["left"]["matching_cases"]==4 and r["right"]["matching_cases"]==6
    assert r["changes"]==[{
        "heading":"Measure 1","aggregation":"count","value_basis":None,"unit":"cases",
        "before":4,"after":6,"change_decimal":"2","relative_percent_decimal":"50.00",
    }]
    for field in ("numeric_changes_descriptive_only",):
        assert r[field] is True
    for field in ("source_independently_verified", "independent_outcome_or_savings_proven",
                  "execution_authorized","case_identities_joined"):
        assert r[field] is False
    reverse=compare(store,person(),wid,2,1)
    assert reverse["changes"][0]["change_decimal"]=="-2"
    assert reverse["changes"][0]["relative_percent_decimal"]=="-33.33"
    audit=json.dumps(store.db.audit_rows("acme",50))
    assert "analysis.revisions_compared" in audit
    assert "Synthetic case counts" not in audit and "relative_percent_decimal" not in audit


def test_zero_baseline_has_no_spurious_infinity(store):
    wid=two(store,left=0,right=8)
    r=compare(store,person(),wid,1,2)
    assert r["comparable"]
    assert r["changes"][0]["change_decimal"]=="8"
    assert r["changes"][0]["relative_percent_decimal"] is None


def test_group_codes_are_never_joined_even_when_both_say_g1_1(store):
    first=store.publish(person(),published(snap=grouped(4)))
    wid=first["view_id"]
    store.publish(person(),published(expected=1,snap=grouped(6)),wid)
    r=compare(store,person(),wid,1,2)
    assert not r["comparable"]
    assert "group_codes_have_no_cross_export_identity" in r["refusal_reasons"]
    assert r["changes"]==[]
    assert "G1_1" not in json.dumps(r)


def test_different_specs_units_and_metric_versions_withhold_numeric_deltas(store):
    one=plain(4,source="a",spec="b").model_dump(mode="json")
    two=plain(6,source="c",spec="d").model_dump(mode="json")
    r=compare_snapshots({"view_id":"same","viewing_revision":1,"title":"Left",
                          "published_at":"2026-01-01","content_sha256":"1"*64,"snapshot":one},
                         {"view_id":"same","viewing_revision":2,"title":"Right",
                          "published_at":"2026-01-02","content_sha256":"2"*64,"snapshot":two})
    assert not r["comparable"] and "different_analysis_specification" in r["refusal_reasons"]
    assert r["changes"]==[]
    two["analysis_spec_sha256"]=one["analysis_spec_sha256"]
    two["columns"][0]["unit"]="hours"
    r=compare_snapshots({"view_id":"same","viewing_revision":1,"title":"Left",
                          "published_at":"2026","content_sha256":"1"*64,"snapshot":one},
                         {"view_id":"same","viewing_revision":2,"title":"Right",
                          "published_at":"2026","content_sha256":"2"*64,"snapshot":two})
    assert "different_measure_definitions" in r["refusal_reasons"] and not r["changes"]


def test_reused_source_hash_with_conflicting_count_abstains(store):
    wid=two(store,right_source="a")
    r=compare(store,person(),wid,1,2)
    assert not r["comparable"]
    assert r["refusal_reasons"]==["same_fingerprint_but_different_values"]
    assert r["changes"]==[]


def test_metric_coverage_missing_unknowns_and_truncation_are_not_complete(store):
    full=plain(4).model_dump(mode="json")
    altered=plain(6,source="c").model_dump(mode="json")
    altered["rows_truncated"]=True
    left={"view_id":"test","viewing_revision":1,"title":"A",
          "published_at":"2026","content_sha256":"1"*64,"snapshot":full}
    right={"view_id":"test","viewing_revision":2,"title":"B",
           "published_at":"2026","content_sha256":"2"*64,"snapshot":altered}
    assert "bounded_rows_are_not_complete" in compare_snapshots(left,right)["refusal_reasons"]
    altered["rows_truncated"]=False
    altered["non_finite_results_withheld"]=1
    assert "nonfinite_results_withheld" in compare_snapshots(left,right)["refusal_reasons"]
    altered["non_finite_results_withheld"]=0
    left["snapshot"]["metric_definition_sha256"]=["d"*64]
    right["snapshot"]["metric_definition_sha256"]=["d"*64]
    for side in (left,right):
        side["snapshot"]["columns"][0].update(
            aggregation="avg", value_basis="case_metric", metric_slot=1)
        side["snapshot"]["metric_coverage"]=[{
            "slot":1, "with_value":side["snapshot"]["matching_cases"]-1,
            "without_value":1, "missing_operand":1, "zero_divisor":0, "non_finite":0,
        }]
    result=compare_snapshots(left,right)
    assert not result["comparable"] and "incomplete_metric_coverage" in result["refusal_reasons"]


def test_membership_expiry_role_revoke_tenant_and_revision_security(store):
    wid=two(store)
    for principal in [person("bob"),person("eve",org="foreign"),
                      person("root",org="foreign",scopes={
                          "org:analysis:read","org:analysis:admin"})]:
        with pytest.raises(PublicationError) as exc:
            compare(store,principal,wid,1,2)
        assert exc.value.status==404
    with pytest.raises(PublicationError) as exc:
        compare(store,person("bob",scopes={"org:process:read"}),wid,1,2)
    assert exc.value.status==403
    store.access(person(),wid,Access(
        expected_access_revision=1,members=[{"actor_id":"bob","role":"viewer"}],
        user_confirmed=True))
    assert compare(store,person("bob",scopes={"org:analysis:read"}),wid,1,2)["comparable"]
    for before,after in [(1,1),(0,2),(1,11),(True,2)]:
        with pytest.raises(PublicationError) as exc:
            compare(store,person(),wid,before,after)
        assert exc.value.status==422
    store.access(person(),wid,Access(expected_access_revision=2,members=[],user_confirmed=True))
    with pytest.raises(PublicationError) as exc:
        compare(store,person("bob"),wid,1,2)
    assert exc.value.status==404


def test_corrupt_revision_refuses_to_claim_a_comparison(store):
    wid=two(store)
    with store.db.connect() as conn:
        store.q(conn,"UPDATE gateway_analysis_revisions SET payload=? WHERE organization_id=? AND view_id=? AND revision=?",
                (json.dumps(plain(12).model_dump(mode="json")),"acme",wid,1))
    with pytest.raises(PublicationError,match="integrity check"):
        compare(store,person(),wid,1,2)
    assert not any(a["action"]=="analysis.revisions_compared"
                   for a in store.db.audit_rows("acme",50))


def test_http_uses_current_auth_and_only_exposes_authorized_revision_pair(tmp_path):
    h=lambda who:{"Authorization":"Bearer "+who}
    with TestClient(app_factory(tmp_path)) as client:
        base="/v1/human/analyses"
        first=client.post(base,headers=h("alice"),json=published().model_dump(mode="json"))
        assert first.status_code==200,first.text
        wid=first.json()["view_id"]
        other=client.post(base+"/"+wid,headers=h("alice"),
             json=published(8,expected=1,source="c").model_dump(mode="json"))
        assert other.status_code==200,other.text
        path=base+"/"+wid+"/compare?left=1&right=2"
        for who,expected in [("read-only",404),("process-only",403),("bob",404),
                              ("foreign",404),("bad",401)]:
            assert client.get(path,headers=h(who)).status_code==expected
        assert client.get(path,headers=h("alice")).status_code==200
        result=client.get(path,headers=h("alice"))
        assert result.headers["cache-control"]=="no-store"
        assert result.json()["changes"][0]["change_decimal"]=="4"
        assert client.get(base+"/"+wid+"/compare?left=2&right=2",
                          headers=h("alice")).status_code==422
        assert client.get(base+"/"+wid+"/compare?left=1&right=999",
                          headers=h("alice")).status_code==422
        assert client.get(base+"/ana_"+"0"*32+"/compare?left=1&right=2",
                          headers=h("alice")).status_code==404


def test_ui_includes_comparison_controls_with_escape_safe_text_rendering():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1]/"gateway"/"analyses.html").read_text(encoding="utf-8")
    assert "compareOther" in source and "compareRun" in source
    assert "/compare?left=" in source and "compareResult" in source
    assert "group_codes_have_no_cross_export_identity" in source
    assert "innerHTML" not in source and "localStorage" not in source
