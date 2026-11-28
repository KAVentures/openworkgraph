"""Portable linkage parity with live Gateway review and exact local retention."""
from copy import deepcopy
import json
import pytest

from shared.connector_bundle_import import inspect
from shared.connector_event_snapshot import digest
from gateway.connector_portable_linkage import PortableExport,export_portable
from gateway.connector_case_review import Plan,Export
from gateway.connector_event_publications import Remove
from gateway.connector_jobs import JobError
from server import enterprise_connector_bundle_import as importer
from server import event_log_workspace as ws
from server.enterprise_rest_feed import FeedError
from test_connector_case_review import linked,approve
from test_connector_event_publications import deployed,source,grant,upload,pin
from test_gateway_connector_jobs import db


def portable(reviewed,spec,**kw):
    report=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    request=PortableExport.model_validate({**spec,"expected_review_sha256":report["review_sha256"],
        "confirm_link_assertions":True,"confirm_export":True,"confirm_complete_source_disclosure":True,**kw})
    return export_portable(reviewed,"customer",request,"owner")


def raw(value):
    return json.dumps(value).encode("utf-8")


def retain_args(value,target=None,request="b"*32):
    data=raw(value);p=importer.preview(data,target)
    return data,{"file_sha256":p["file_sha256"],"canonical_bundle_sha256":p["canonical_bundle_sha256"],
                 "expected_revision":p["expected_target_revision"],"target":target,
                 "name":None if target else "Portable linked cases","request_id":request,
                 "confirm_retention":True,"confirm_origin_review":True,"confirm_linkage_review":True}


def test_exact_live_export_and_offline_projection_parity(linked):
    reviewed,spec,_,_=linked
    package=portable(reviewed,spec)
    report=reviewed.preview("customer",Plan.model_validate(spec),"owner")
    direct=reviewed.export("customer",approve(spec,report),"owner")
    projected,preview=inspect(raw(package))
    assert [r.model_dump() for r in projected.events]==direct["events"]
    assert projected.mapping==direct["mapping"]
    assert preview["linkage_summary"]==report["summary"]
    assert preview["review_sha256"]==report["review_sha256"]
    assert preview["manifest_and_row_integrity_verified"]
    assert preview["linkage_projection_integrity_verified"]
    assert not preview["gateway_origin_authenticated"] and not preview["business_identity_verified"]
    assert preview["source_event_namespace_isolation"]
    assert preview["source_local_case_count"]==2
    assert preview["retained_projection_case_count"]==1


def test_local_atomic_linked_import_receipt_and_replay(linked):
    reviewed,spec,_,_=linked
    package=portable(reviewed,spec)
    data,args=retain_args(package)
    before=ws.list_logs()
    with pytest.raises(FeedError,match="explicit_offline_linkage_review"):
        importer.retain(data,**{**args,"confirm_linkage_review":False})
    assert ws.list_logs()==before
    r=importer.retain(data,**args)
    assert r["log"]["events"]==4 and r["log"]["objects"]==1
    assert r["receipt"]["review_sha256"]==package["review_sha256"]
    assert r["receipt"]["bundle_kind"]=="reviewed_linkage"
    target=r["log"]["log_id"]
    assert r["log"]["connector_bundle_import"]["bundle_kind"]=="reviewed_linkage"
    assert len({n["case_namespace"] for n in r["log"]["source_identity"]["namespaces"]})==1
    assert importer.retain(data,**args)["replayed_receipt"]
    data2,args2=retain_args(package,target=target,request="c"*32)
    assert importer.retain(data2,**args2)["replayed_receipt"]
    assert len(importer.history(target)["receipts"])==1
    changed=portable(reviewed,{**spec,"groups":[{**spec["groups"][0],"basis":"operator_assertion"}]})
    with pytest.raises(FeedError,match="changed_linkage_requires_new_log"):
        importer.preview(raw(changed),target)
    ws.delete_log(target)
    assert not any(x["log_id"]==target for x in importer.inventory()["logs"])


def test_excluded_rows_disclosed_only_with_separate_consent_and_not_retained(linked,deployed):
    reviewed,spec,_,_=linked
    j,token,snap,_=source(deployed)
    g=grant(reviewed.publications,j,snap);upload(reviewed.publications,token,g,snap)
    restricted={**spec,"snapshots":[*spec["snapshots"],pin(g)],"include_unlinked":False}
    with pytest.raises(JobError,match="explicit_complete_source_disclosure"):
        portable(reviewed,restricted,confirm_complete_source_disclosure=False)
    package=portable(reviewed,restricted)
    assert len(package["source_bundle"]["events"])==6
    p=importer.preview(raw(package))
    assert p["complete_source_rows_in_file"]==6 and p["excluded_source_rows_not_retained"]==2
    assert p["event_count"]==4
    data,args=retain_args(package)
    r=importer.retain(data,**args)
    assert r["log"]["events"]==4 and r["log"]["objects"]==1
    assert g["job_id"] not in {n["source_namespace"] for n in r["log"]["source_identity"]["namespaces"]}


@pytest.mark.parametrize("mutate",[
    lambda d:d["source_bundle"]["events"][0].update(activity="tampered"),
    lambda d:d["pins"][0].update(expires_at=123),
    lambda d:d["pins"][0].update(publication_revision=99),
    lambda d:d["groups"][0].update(basis="operator_assertion"),
    lambda d:d["groups"][0]["members"][0].update(case_id="f"*64),
    lambda d:d["groups"].append(deepcopy(d["groups"][0])),
    lambda d:d.update(organization_id="different"),
    lambda d:d.update(include_unlinked=False),
    lambda d:d.update(confirm_many_to_one=True),
    lambda d:d.update(review_sha256="f"*64),
    lambda d:d.update(scope_sha256="f"*64),
    lambda d:d.update(projected_rows_sha256="f"*64),
])
def test_changed_package_fields_fail_closed(linked,mutate):
    reviewed,spec,_,_=linked
    value=portable(reviewed,spec);mutate(value)
    with pytest.raises(ValueError,match="portable_linkage_integrity_failure"):
        inspect(raw(value))


def test_permutations_preserve_canonical_package_and_projection(linked):
    reviewed,spec,_,_=linked
    original=portable(reviewed,spec)
    value=deepcopy(original)
    value["pins"].reverse();value["groups"][0]["members"].reverse()
    value["source_bundle"]["events"].reverse();value["source_bundle"]["manifests"].reverse()
    a,pa=inspect(raw(original));b,pb=inspect(raw(value))
    assert pa["canonical_bundle_sha256"]==pb["canonical_bundle_sha256"]
    assert pa["file_sha256"]!=pb["file_sha256"]
    assert a==b


def test_revocation_prevents_new_export_but_not_offline_verification(linked):
    reviewed,spec,ga,_=linked
    package=portable(reviewed,spec)
    reviewed.publications.remove("customer",ga["job_id"],Remove(expected_publication_revision=ga["revision"],
        confirm_delete=True),"owner",ga["grant_id"])
    with pytest.raises(JobError,match="snapshot_missing_or_changed"):
        portable(reviewed,spec)
    _,p=inspect(raw(package))
    assert not p["live_grants_verified"]
