"""Verify complete portable bundles, then retain exact source keys and receipts."""
from copy import deepcopy
from io import BytesIO
import json
import pytest

from shared.connector_bundle_import import inspect,decode,MAPPING
from shared.connector_event_snapshot import digest,manifest_digest
from server import enterprise_connector_bundle_import as importer
from server import event_log_workspace as ws
from server.enterprise_rest_feed import FeedError
from gateway.connector_event_publications import bundle_csv
from test_connector_event_publications import deployed,source,grant,upload,bundle
from test_gateway_connector_jobs import db


def raw(value):
    return json.dumps(value,ensure_ascii=True).encode()


@pytest.fixture
def published(deployed):
    jobs,store=deployed
    a,at,sa,_=source(deployed,labels=True)
    b,bt,sb,_=source(deployed)
    ga=grant(store,a,sa,confirm_activity_labels=True);gb=grant(store,b,sb)
    upload(store,at,ga,sa);upload(store,bt,gb,sb)
    return bundle(store,ga,gb)


def approved(value,*,target=None,name="Enterprise fixture",request="a"*32,**changes):
    data=raw(value)
    p=importer.preview(data,target)
    args={"file_sha256":p["file_sha256"],"canonical_bundle_sha256":p["canonical_bundle_sha256"],
          "target":target,"name":None if target else name,"expected_revision":p["expected_target_revision"],
          "request_id":request,"confirm_retention":True,"confirm_origin_review":True,**changes}
    return data,args


def create(value):
    data,args=approved(value)
    return importer.retain(data,**args)


def remanifest(value,job):
    s=next(s for s in value["manifests"] if s["job_id"]==job)
    rows=[{k:v for k,v in e.items() if k!="source_namespace"} for e in value["events"] if e["source_namespace"]==job]
    rows.sort(key=lambda e:(e["observed_at"],e["event_id"]))
    s["manifest"].update(event_count=len(rows),case_count=len({r["case_id"] for r in rows}),rows_sha256=digest(rows))
    s["manifest_sha256"]=manifest_digest(s["manifest"])
    value["event_count"]=len(value["events"])


def test_preview_is_counts_only_and_canonical_order_invariant(published):
    before=ws.list_logs()
    p=importer.preview(raw(published))
    reversed_bundle={**published,"events":list(reversed(published["events"])),"manifests":list(reversed(published["manifests"]))}
    other=importer.preview(raw(reversed_bundle))
    assert p["file_sha256"]!=other["file_sha256"]
    assert p["canonical_bundle_sha256"]==other["canonical_bundle_sha256"]
    assert p["event_count"]==4 and p["source_local_case_count"]==2
    assert p["activity_labels_present"] and not p["gateway_origin_authenticated"]
    assert not p["live_grants_verified"] and not p["persisted"]
    for event in published["events"]:
        assert event["event_id"] not in json.dumps(p)
        assert event["case_id"] not in json.dumps(p)
        assert event["activity"] not in json.dumps(p)
    assert ws.list_logs()==before


@pytest.mark.parametrize("mutate",[
    lambda d:d["events"][0].update(activity="tampered"),
    lambda d:d["events"][0].update(event_id="f"*64),
    lambda d:d["events"][0].update(case_id="f"*64),
    lambda d:d["events"][0].update(observed_at="2026-01-01T00:00:00Z"),
    lambda d:d["events"][0].update(source_namespace="cj_"+"f"*32),
    lambda d:d["events"][0].update(resource="unexpected"),
    lambda d:d["mapping"].update(case_namespace="invented"),
    lambda d:d["manifests"][0]["manifest"].update(profile_revision=100),
    lambda d:d["manifests"][0].update(manifest_sha256="f"*64),
    lambda d:d["manifests"].append(d["manifests"][0]),
    lambda d:d["events"].append(d["events"][0]),
    lambda d:d.update(event_count=True),
    lambda d:d.update(provider_completeness_verified=True),
    lambda d:d.update(quality_review={}),
])
def test_tampering_and_unrecognized_semantics_fail_closed(published,mutate):
    changed=deepcopy(published);mutate(changed)
    with pytest.raises(FeedError,match="bundle_manifest_or_row_integrity_failure"):
        importer.preview(raw(changed))


def test_json_duplicate_keys_nonfinite_and_non_utf8_refused():
    for value in (b'{"format":"x","format":"y"}',b'{"x":NaN}',b'\xff',b'['*20000):
        with pytest.raises(FeedError,match="invalid_bundle_json"):
            importer.preview(value)
    with pytest.raises(FeedError,match="unsupported_bundle_format"):
        importer.preview(b'{"format":"openworkgraph.reviewed-cross-source-cases.v1"}')


def test_actual_gateway_export_retains_exact_local_source_identity_and_receipts(published):
    data,args=approved(published)
    result=importer.retain(data,**args)
    log,receipt=result["log"],result["receipt"]
    assert log["events"]==4 and log["objects"]==2
    assert receipt["events_added"]==4 and receipt["duplicates_skipped"]==0
    assert len(log["source_identity"]["namespaces"])==2
    assert log["connector_bundle_import"]["revision"]==1
    assert importer.history(log["log_id"])["receipts"]==[receipt]
    replay=importer.retain(data,**args)
    assert replay["replayed_receipt"] and replay["log"]["log_id"]==log["log_id"]
    assert len(importer.inventory()["logs"])==1
    with pytest.raises(FeedError,match="bundle_import_request_identity_conflict"):
        importer.retain(data,**{**args,"name":"different"})
    ws.delete_log(log["log_id"])
    assert importer.inventory()["logs"]==[]


def test_preview_pin_and_separate_consent(published):
    data,args=approved(published)
    for k in ("confirm_retention","confirm_origin_review"):
        with pytest.raises(FeedError,match="explicit_bundle_retention"):
            importer.retain(data,**{**args,k:False})
    with pytest.raises(FeedError,match="bundle_review_changed"):
        importer.retain(data+b" ",**args)
    with pytest.raises(FeedError,match="bundle_review_changed"):
        importer.retain(data,**{**args,"canonical_bundle_sha256":"a"*64})
    assert importer.inventory()["logs"]==[]


def later(value):
    v=deepcopy(value);src=v["manifests"][0];job=src["job_id"]
    e=next(e for e in v["events"] if e["source_namespace"]==job)
    v["events"].append({**e,"event_id":"f"*64})
    src["grant_id"]="cep_"+"f"*32
    remanifest(v,job)
    return v


def test_incremental_append_preserves_source_keys_and_exact_replay(published):
    first=create(published)
    target=first["log"]["log_id"]
    next_bundle=later(published)
    data,args=approved(next_bundle,target=target,request="b"*32)
    result=importer.retain(data,**args)
    assert result["log"]["events"]==5 and result["log"]["objects"]==2
    assert result["receipt"]["events_added"]==1 and result["receipt"]["duplicates_skipped"]==4
    assert result["receipt"]["revision"]==2
    assert importer.retain(data,**args)["replayed_receipt"]
    assert len(importer.history(target)["receipts"])==2
    replay_data,replay_args=approved(published,target=target,request="c"*32)
    assert importer.retain(replay_data,**replay_args)["replayed_receipt"]
    assert len(importer.history(target)["receipts"])==2


def test_scope_and_grant_conflicts_fail_before_retention(published):
    target=create(published)["log"]["log_id"]
    changed=later(published);src=changed["manifests"][0]
    src["manifest"]["profile_revision"]+=1
    remanifest(changed,src["job_id"])
    with pytest.raises(FeedError,match="bundle_source_scope_changed"):
        importer.preview(raw(changed),target)
    changed=later(published)
    changed["manifests"][0]["grant_id"]=published["manifests"][0]["grant_id"]
    with pytest.raises(FeedError,match="bundle_grant_manifest_conflict"):
        importer.preview(raw(changed),target)


def test_conflicting_existing_event_rolls_back_receipt_and_log(published):
    target=create(published)["log"]["log_id"]
    changed=later(published);src=changed["manifests"][0];job=src["job_id"]
    old=next(e for e in changed["events"] if e["source_namespace"]==job)
    old["case_id"]="d"*64
    remanifest(changed,job)
    data,args=approved(changed,target=target,request="b"*32)
    with pytest.raises(FeedError):
        importer.retain(data,**args)
    h=importer.history(target)
    assert len(h["receipts"])==1 and h["log"]["events"]==4


def test_generic_append_invalidates_controlled_append_even_if_all_duplicates(published):
    target=create(published)["log"]["log_id"]
    ws.append_log(target,BytesIO(bundle_csv(published).encode()),source_format="csv",mapping=MAPPING)
    with pytest.raises(FeedError,match="target_log_changed_outside_verified_import"):
        importer.preview(raw(later(published)),target)


def test_stale_target_revision_requires_new_preview(published):
    target=create(published)["log"]["log_id"]
    data,args=approved(later(published),target=target,request="b"*32)
    importer.retain(data,**args)
    with pytest.raises(FeedError,match="bundle_target_revision_changed"):
        importer.retain(data,**{**args,"request_id":"c"*32})
