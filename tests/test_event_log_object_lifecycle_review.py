"""Exact lifecycle evidence uses the same selected replay population as conformance."""
from __future__ import annotations

from copy import deepcopy
import pytest

from server import event_log_object_lifecycle_review as lifecycle
from server import event_log_object_process_model as model
from server import event_log_workspace as workspace
from test_event_log_objects import _ocel, _order_to_cash
from test_event_log_object_process_model import _deviating


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(workspace,"DATA_DIR",tmp_path)


def setup(kind="order",**options):
    reference=model.discover(_ocel(_order_to_cash()))["model"]
    doc=_deviating()
    doc["objects"].append({"id":"isolated-order","type":"order"})
    log=_ocel(doc)
    spec={"model":reference,"object_type":kind,**options}
    return log,spec


def request(log,spec,report,view,**options):
    return lifecycle.review(log,{**spec,"view":view,"expected_review_sha256":report["review_sha256"],**options})


def test_exact_registered_population_and_agreement_with_existing_conformance():
    log,spec=setup()
    summary=lifecycle.review(log,spec)
    old=next(t for t in model.check(log,{"model":spec["model"]})["lifecycles"] if t["object_type"]=="order")
    assert summary["registered_objects"]==4
    assert summary["fitting_objects"]==old["fitting_objects"]==1
    assert summary["deviating_objects"]==old["replayed_objects"]-old["fitting_objects"]==2
    assert summary["unknown_objects"]==1
    assert summary["unknown_reasons"]=={"no_visible_events":1}
    assert summary["fitting_share_bounds"]=={"lower":.25,"upper":.5}
    assert summary["binding_based_replay"] is False
    assert summary["execution_authorized"] is False


def test_every_variant_and_membership_matches_paginated_objects():
    log,spec=setup()
    summary=lifecycle.review(log,spec)
    variants=request(log,spec,summary,"variants",limit=1)
    all_variants=list(variants["variants"])
    while variants["next_offset"] is not None:
        variants=request(log,spec,summary,"variants",limit=1,offset=variants["next_offset"])
        all_variants.extend(variants["variants"])
    seen=set()
    for variant in all_variants:
        members=request(log,spec,summary,"objects",variant_sha256=variant["variant_sha256"])
        assert members["total"]==variant["objects"]
        for obj in members["objects"]:
            assert obj["object_id"] not in seen
            seen.add(obj["object_id"])
            detail=request(log,spec,summary,"object",object_id=obj["object_id"],variant_sha256=variant["variant_sha256"])
            assert [e["activity"] for e in detail["events"]]==variant["path"]
            assert detail["token_replay"]["fits"]==(variant["status"]=="fitting")
    assert seen=={"o1","o2","o3"}
    unknown=request(log,spec,summary,"objects",status="unknown")
    assert [o["object_id"] for o in unknown["objects"]]==["isolated-order"]
    detail=request(log,spec,summary,"object",object_id="isolated-order",status="unknown")
    assert detail["token_replay"] is None and detail["events"]==[]


def test_budget_excluded_variants_are_not_replayed_individually():
    log,spec=setup(max_variants=1)
    summary=lifecycle.review(log,spec)
    assert summary["unknown_reasons"]["variant_limit"]>=1
    unknown=request(log,spec,summary,"objects",status="unknown")
    omitted=next(o for o in unknown["objects"] if o["unknown_reason"]=="variant_limit")
    detail=request(log,spec,summary,"object",object_id=omitted["object_id"])
    assert detail["events"] and detail["token_replay"] is None


def test_exact_event_paging_and_ties():
    doc=_deviating()
    doc["events"].append({"id":"tie","type":"Pay","time":"2024-01-03T07:00:00Z",
                          "relationships":[{"objectId":"o3","qualifier":"q"}]})
    log=_ocel(doc)
    spec={"model":model.discover(_ocel(_order_to_cash()))["model"],"object_type":"order"}
    summary=lifecycle.review(log,spec)
    first=request(log,spec,summary,"object",object_id="o3",limit=1)
    assert first["events"][0]["timestamp_tied"]
    second=request(log,spec,summary,"object",object_id="o3",limit=1,offset=first["next_offset"])
    assert second["events"][0]["timestamp_tied"]
    assert first["events"][0]["event_id"]<second["events"][0]["event_id"]


def test_changed_model_or_source_refuses_existing_evidence():
    log,spec=setup()
    summary=lifecycle.review(log,spec)
    changed=deepcopy(spec)
    changed["max_variants"]=1
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        request(log,changed,summary,"objects")
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            conn.execute("UPDATE events SET activity='Changed' WHERE log_id=? AND event_id='o3-pay'",[log])
        finally:
            conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        request(log,spec,summary,"objects")


def test_work_budget_refuses_instead_of_partial_review(monkeypatch):
    log,spec=setup()
    monkeypatch.setattr(lifecycle,"MAX_PROJECTED_EVENTS",0)
    with pytest.raises(workspace.EventLogWorkspaceError,match="budget"):
        lifecycle.review(log,spec)


def test_strict_scope_and_unreviewed_evidence():
    log,spec=setup()
    summary=lifecycle.review(log,spec)
    with pytest.raises(workspace.EventLogWorkspaceError,match="before"):
        lifecycle.review(log,{**spec,"view":"objects"})
    with pytest.raises(workspace.EventLogWorkspaceError,match="absent"):
        request(log,spec,summary,"object",object_id="i1")
    with pytest.raises(workspace.EventLogWorkspaceError,match="Variant"):
        request(log,spec,summary,"objects",variant_sha256="0"*64)
    with pytest.raises(workspace.EventLogWorkspaceError,match="absent"):
        request(log,spec,summary,"object",object_id="o3",status="fitting")
    with pytest.raises(workspace.EventLogWorkspaceError,match="Invalid"):
        lifecycle.review(log,{**spec,"limit":101})
