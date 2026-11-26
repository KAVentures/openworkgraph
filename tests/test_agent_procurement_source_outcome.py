from copy import deepcopy
from datetime import datetime, timezone
from itertools import product

import pytest

from shared.agent_procurement_source_outcome import project,summarize
from server import agent_procurement_source_outcome as source
from server import agent_procurement_monitor_journal as journal
from test_agent_procurement_monitor_journal import local_db,doc,monitor,pilot,version_change,rehearsal,trial,study


def iso(ms):
    return datetime.fromtimestamp(ms/1000,timezone.utc).isoformat()


def receipt(paid=2000,read=4500):
    return {"provider":"stripe","availability":"source_reported","verified_at":iso(read),
        "verification_method":"opted_in_restricted_key_stripe_account_and_record_get","source_state_reported":True,
        "object_type":"invoice","lifecycle_projection_diagnostics":{"chronology_conflict":False,"invalid_timestamp_field_count":0},
        "process_audit_events":[{"action":"invoice_paid","observed_at":iso(paid)}],
        "source_state":"paid","verified_business_outcome":True}


def test_exact_window_projection():
    for start,end,paid,read,state,verified,diag in product((1000,1500,2000),(2000,2500,3000,3500),
            (0,1000,2000,3000,4000),(1500,2500,3500,4500,5500),("paid","open"),(True,False),(True,False)):
        if start>=end:continue
        row=receipt(paid,read);row["source_state"]=state;row["verified_business_outcome"]=verified
        row["lifecycle_projection_diagnostics"]["chronology_conflict"]=not diag
        old=deepcopy(row);actual=project(row,start,end,4500)
        expected=end<=read<=4500 and diag and state=="paid" and verified and paid<=read and set(range(paid,paid+1000)).issubset(range(start,end))
        assert (actual["status"]=="confirmed")==expected
        assert row==old


def test_conflict_projection():
    for check,status,outcome in product((None,"confirmed","unknown"),(None,"failed","completed","withdrawn"),(None,"success","failure","unresolved")):
        s={"source_outcome_bindings":[{"slot_id":"s","window_id":"w"}],
            "source_outcome_checks":[] if check is None else [{"slot_id":"s","status":check}],
            "snapshot":{"windows":[{"observations":[] if status is None else [{"slot_id":"s","status":status,"business_outcome":outcome}]}]}}
        old=deepcopy(s);a=summarize(s)
        assert a["blocks_continuation"]==(check=="confirmed" and (status=="failed" or status=="completed" and outcome=="failure"))
        assert a["confirmed"]==int(check=="confirmed") and a["unknown"]==int(check!="confirmed")
        assert s==old


REF="owg:r:"+"a"*32
RECORD={"provider":"stripe","resource_kind":"invoice","host":"dashboard.stripe.com","resolver_locator":"invoice:in_abcdefghijk"}


def pins(view):
    return {"monitor_id":view["monitor_id"],"window_id":"w1","expected_head_sha256":view["head_sha256"],
            "expected_report_sha256":view["latest_report"]["report_sha256"],"confirmed":True}


def bind_doc(view,slot):
    return {**pins(view),"confirm_operator_case_mapping":True,"confirm_case_outcome_defined_by_predicate":True,
            "bindings":[{"slot_id":slot["slot_id"],"case_token_sha256":slot["case_token_sha256"],
                         "resource_ref":REF,"predicate":"invoice_paid_in_window"}]}


def test_bound_read_and_immutable_confirmation(local_db,monitor,monkeypatch):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    monkeypatch.setattr(source,"ensure_api_token",lambda:"local-test-secret")
    monkeypatch.setattr(source,"resolve_resource_reference",lambda ref:dict(RECORD))
    slot=plan["windows"][0]["plan"]["slots"][0]
    calls=[]
    def reader(ref,*,include_lifecycle,resolver):
        calls.append(ref)
        assert ref==REF and include_lifecycle and resolver(ref)==RECORD and resolver("other") is None
        return receipt()
    monkeypatch.setattr(source.stripe_source_evidence,"stripe_record_receipt",reader)
    bound=source.bind("process_1",1,bind_doc(view,slot))
    assert not calls and bound["source_outcomes"]["unknown"]==1
    assert RECORD["resolver_locator"] not in str(bound)
    check={**pins(bound),"slot_ids":[slot["slot_id"]],"confirm_source_read":True}
    result=source.check("process_1",1,check)
    assert calls==[REF] and result["source_outcomes"]["confirmed"]==1
    assert not result["source_outcomes"]["causal_business_success_verified"]
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==result
    with pytest.raises(ValueError,match="immutable"):
        source.check("process_1",1,{**check,**pins(result)})
    assert calls==[REF]


def test_changed_mapping_refused_before_network(local_db,monitor,monkeypatch):
    packet,initial,plan,snapshot=monitor
    view=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    monkeypatch.setattr(source,"ensure_api_token",lambda:"local-test-secret")
    monkeypatch.setattr(source,"resolve_resource_reference",lambda ref:dict(RECORD))
    slot=plan["windows"][0]["plan"]["slots"][0]
    bound=source.bind("process_1",1,bind_doc(view,slot))
    monkeypatch.setattr(source,"resolve_resource_reference",lambda ref:{**RECORD,"resolver_locator":"invoice:in_otherrecord1"})
    monkeypatch.setattr(source.stripe_source_evidence,"stripe_record_receipt",lambda *a,**k:pytest.fail("unexpected network read"))
    with pytest.raises(ValueError,match="identity changed"):
        source.check("process_1",1,{**pins(bound),"slot_ids":[slot["slot_id"]],"confirm_source_read":True})
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==bound
