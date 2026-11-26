"""Actual signed dossiers and evidence-pinned historical review referrals."""
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_audit_triage as triage
from server import agent_procurement_workforce_audit_review as workbench
from shared.agent_procurement import digest
from test_agent_procurement_workforce_timeline import snapshot,manifest
from test_agent_procurement_workforce_exchange import row


def evidence():
    a,b,c=[Ed25519PrivateKey.generate() for _ in range(3)]
    before=row("a",incident=True);before["cutoff_ms"]=100
    after=row("a",case="changed",eligible=False);after["cutoff_ms"]=70
    first=snapshot({"a":(a,[before]),"b":(b,[row("b")])},110)
    second=snapshot({"a":(c,[after])},130,age=200)
    m=manifest(first,second)
    return {"confirmed":True,"expected_manifest_sha256":digest(m),
            "manifest":m,"dossiers":[first[0],second[0]]}


def choice(f,action="investigate",owner="ops"):
    return {"finding_id":f["finding_id"],"expected_finding_sha256":f["finding_sha256"],
            "disposition":action,"owner_alias":owner,"external_reference":None}


def decision(w,*choices):
    return {"expected_worklist_sha256":w["worklist_sha256"],"reviewer_alias":"reviewer",
            "confirmed":True,"selections":list(choices)}


def test_complete_deterministic_findings_and_exact_evidence_pins():
    doc=evidence()
    w=workbench.review_timeline(doc)["worklist"]
    assert w==workbench.review_timeline(doc)["worklist"]
    expected={"rollback_referral","cutoff_regression","cohort_change","excluded_journal",
              "journal_absent_latest","site_absent","key_change","freshness_policy_change"}
    assert set(w["counts_by_kind"])==expected
    assert w["finding_count"]==8
    assert w["findings"][0]["kind"]=="rollback_referral"
    assert w["worklist_sha256"]==digest({k:v for k,v in w.items() if k!="worklist_sha256"})
    for f in w["findings"]:
        assert f["finding_sha256"]==digest({k:v for k,v in f.items() if k!="finding_sha256"})
        for pin in f["evidence"]["snapshots"]:
            selected=doc["dossiers"][pin["snapshot"]-1]
            assert pin["dossier_sha256"]==selected["dossier_sha256"]
            assert pin["report_sha256"]==selected["report"]["report_sha256"]


def test_partial_full_and_no_clearance():
    doc=evidence();w=workbench.review_timeline(doc)["worklist"]
    first=w["findings"][0];other=w["findings"][1]
    result=workbench.record_triage({**doc,"decision":decision(w,choice(other))})
    assert result["selected_count"]==1 and result["unselected_count"]==7
    assert result["unselected_rollback_referrals"]==1
    assert not result["all_findings_addressed_in_receipt"]
    full=workbench.record_triage({**doc,"decision":decision(w,*(choice(f) for f in w["findings"]))})
    assert full["unselected_count"]==0 and full["all_findings_addressed_in_receipt"]
    assert full["unselected_rollback_referrals"]==0
    assert not full["incident_clearance_established"] and not full["execution_authorized"]
    assert not full["reviewer_identity_verified"] and not full["external_ticket_created"]
    assert not full["result_persisted"]
    assert full["triage_sha256"]==digest({k:v for k,v in full.items() if k!="triage_sha256"})
    assert result["dispositions"][0]["finding_id"]==other["finding_id"]


@pytest.mark.parametrize("kind",["wrong_worklist","wrong_finding","unknown_finding","duplicate",
    "rollback_acknowledge","missing_owner","false_confirm","coerced_confirm","bad_disposition",
    "external_url","extra_field","empty","altered_evidence","outer_confirmation"])
def test_decision_rejects_stale_incomplete_or_unsupported_requests(kind):
    doc=evidence();w=workbench.review_timeline(doc)["worklist"]
    selection=choice(w["findings"][0])
    d=decision(w,selection)
    if kind=="wrong_worklist":d["expected_worklist_sha256"]="0"*64
    elif kind=="wrong_finding":selection["expected_finding_sha256"]="0"*64
    elif kind=="unknown_finding":selection["finding_id"]="0"*64
    elif kind=="duplicate":d["selections"].append(deepcopy(selection))
    elif kind=="rollback_acknowledge":selection["disposition"]="acknowledge"
    elif kind=="missing_owner":selection["owner_alias"]=None
    elif kind=="false_confirm":d["confirmed"]=False
    elif kind=="coerced_confirm":d["confirmed"]="true"
    elif kind=="bad_disposition":selection["disposition"]="close"
    elif kind=="external_url":selection["external_reference"]="https://example.com/ticket"
    elif kind=="extra_field":selection["closed"]=True
    elif kind=="empty":d["selections"]=[]
    elif kind=="altered_evidence":doc["dossiers"][0]["report"]["included_journals"]=9
    else:doc["confirmed"]=False
    with pytest.raises(ValueError):workbench.record_triage({**doc,"decision":d})


def test_no_warning_worklist_and_unreadable_counts():
    from server import agent_procurement_workforce_dossier as audit
    from server import agent_procurement_workforce_signing as signing
    from test_agent_procurement_workforce_exchange import request,pinned
    from test_agent_procurement_workforce_signing import trust
    key=Ed25519PrivateKey.generate()
    x=snapshot({"a":(key,[row("a")])},100)
    y=snapshot({"a":(key,[row("a")])},120)
    m=manifest(x,y)
    assert triage.worklist([x[0],y[0]],m,expected_manifest_sha256=digest(m))["finding_count"]==0
    req=request(signing.sign_packet(key,pinned("a",[row("a")],when=120,unreadable=1)["packet"]))
    req["trust"]=[trust("a",key)]
    d=audit.freeze(req,now=130)
    m["entries"][1]["dossier_sha256"]=d["dossier_sha256"]
    w=triage.worklist([x[0],d],m,expected_manifest_sha256=digest(m))
    assert w["finding_count"]==1
    assert w["findings"][0]["kind"]=="unreadable_journals"
    assert w["findings"][0]["evidence"]["details"]["declared_unreadable_journals"]==1


def test_offline_cli_recomputes_before_receipt(tmp_path):
    from server import agent_procurement_workforce_dossier as audit
    doc=evidence();m=tmp_path/"manifest.json";audit.write_json(m,doc["manifest"])
    paths=[]
    for i,d in enumerate(doc["dossiers"]):
        p=tmp_path/f"{i}.json";audit.write_json(p,d);paths.append(str(p))
    w=tmp_path/"worklist.json";r=tmp_path/"receipt.json";p=tmp_path/"decision.json"
    args=[str(m),"--manifest-sha256",doc["expected_manifest_sha256"]]
    assert triage.main([*args,"--output",str(w),*paths])==0
    work=audit.read_json(w)
    audit.write_json(p,decision(work,choice(work["findings"][0])))
    assert triage.main([*args,"--output",str(r),"--decision",str(p),*paths])==0
    assert audit.read_json(r)["selected_count"]==1
    with pytest.raises(SystemExit):
        triage.main([*args,"--output",str(r),"--decision",str(p),*paths])


def test_source_attention_retained_when_later_snapshot_clean():
    key=Ed25519PrivateKey.generate()
    old=row("a");old["attention"]=True
    x=snapshot({"a":(key,[old])},100)
    y=snapshot({"a":(key,[row("a")])},120)
    m=manifest(x,y)
    result=workbench.review_timeline({"confirmed":True,"expected_manifest_sha256":digest(m),
                                    "manifest":m,"dossiers":[x[0],y[0]]})
    assert result["timeline"]["journals"][0]["source_attention_snapshots"]==[1]
    assert result["worklist"]["counts_by_kind"]=={"source_attention":1}
    assert result["worklist"]["findings"][0]["evidence"]["snapshots"][0]["snapshot"]==1
