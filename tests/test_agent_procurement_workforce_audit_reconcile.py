"""Signed historical input plus independently pinned unsigned triage receipts."""
from copy import deepcopy
from itertools import permutations

import pytest

from server import agent_procurement_workforce_audit_reconcile as reconciliation
from server import agent_procurement_workforce_audit_review as workbench
from shared.agent_procurement import digest
from test_agent_procurement_workforce_audit_triage import evidence,choice,decision


def fixture():
    doc=evidence()
    work=workbench.review_timeline(doc)["worklist"]
    return doc,work


def make_receipt(doc,work,f,*,reviewer="reviewer",action="investigate",owner="ops",ref=None):
    item=choice(f,action=action,owner=owner)
    item["external_reference"]=ref
    request=decision(work,item)
    request["reviewer_alias"]=reviewer
    receipt=workbench.record_triage({**doc,"decision":request})
    return {"expected_triage_sha256":receipt["triage_sha256"],"receipt":receipt}


def run(doc,*receipts):
    return workbench.reconcile_receipts({**doc,"receipt_set":{"confirmed":True,"receipts":list(receipts)}})


def test_conflict_retains_all_declarations_order_invariant():
    doc,work=fixture()
    f,g=work["findings"][:2]
    a=make_receipt(doc,work,f,reviewer="r1")
    b=make_receipt(doc,work,f,reviewer="r2",owner="security",action="escalate",ref="T-1")
    c=make_receipt(doc,work,g,reviewer="r1")
    original=deepcopy((doc,a,b,c))
    baseline=run(doc,a,b,c)
    for order in permutations((a,b,c)):
        assert run(doc,*order)==baseline
    assert (doc,a,b,c)==original
    rows={r["finding_id"]:r for r in baseline["findings"]}
    assert rows[f["finding_id"]]["status"]=="conflicting_declarations"
    assert rows[f["finding_id"]]["conflict_dimensions"]==["disposition","owner_alias","external_reference"]
    assert rows[f["finding_id"]]["declared_reviewer_alias_count"]==2
    assert len(rows[f["finding_id"]]["declarations"])==2
    assert rows[g["finding_id"]]["status"]=="one_declaration"
    assert baseline["counts_by_status"]["unaddressed"]==work["finding_count"]-2
    assert sum(baseline["counts_by_status"].values())==work["finding_count"]
    assert baseline["conflicting_rollback_referrals"]==1
    assert baseline["unaddressed_rollback_referrals"]==0
    assert not baseline["conflicts_resolved"] and not baseline["incident_clearance_established"]
    assert not baseline["reviewer_independence_verified"] and not baseline["work_completed_verified"]
    assert not baseline["external_ticket_created"] and not baseline["result_persisted"]
    assert baseline["reconciliation_sha256"]==digest({k:v for k,v in baseline.items() if k!="reconciliation_sha256"})


def test_matching_alias_variants_and_same_alias_conflicts():
    doc,work=fixture();f=work["findings"][0]
    a=make_receipt(doc,work,f,reviewer="Alice")
    b=make_receipt(doc,work,f,reviewer="alice")
    matching=run(doc,a,b)
    row=matching["findings"][0]
    assert row["status"]=="matching_declarations"
    assert row["declared_reviewer_alias_count"]==1
    assert matching["casefold_reviewer_alias_variants"]==[["Alice","alice"]]
    assert not row["same_reviewer_conflict"]
    c=make_receipt(doc,work,f,reviewer="Alice",owner="security")
    conflict=run(doc,a,b,c)["findings"][0]
    assert conflict["same_reviewer_conflict"]
    assert conflict["conflict_dimensions"]==["owner_alias"]


def test_unaddressed_rollback_preserved():
    doc,work=fixture()
    r=run(doc,make_receipt(doc,work,work["findings"][1]))
    assert r["unaddressed_rollback_referrals"]==1
    assert r["findings"][0]["declarations"]==[]


def repin(entry):
    receipt=entry["receipt"]
    pin=digest({k:v for k,v in receipt.items() if k!="triage_sha256"})
    receipt["triage_sha256"]=entry["expected_triage_sha256"]=pin


@pytest.mark.parametrize("kind",["wrong_pin","duplicate","rewritten_count","changed_scope","changed_priority",
    "closed","unknown_selection","missing_field","coerced_bool","reordered_evidence","wrong_worklist",
    "missing_confirmation","empty","too_many"])
def test_fail_closed_even_when_attacker_rehashes(kind):
    doc,work=fixture();f=work["findings"][0]
    a=make_receipt(doc,work,f);entries=[a]
    if kind=="wrong_pin":a["expected_triage_sha256"]="0"*64
    elif kind=="duplicate":entries.append(deepcopy(a))
    elif kind=="rewritten_count":a["receipt"]["selected_count"]=100;repin(a)
    elif kind=="changed_scope":a["receipt"]["dispositions"][0]["scope"]={};repin(a)
    elif kind=="changed_priority":a["receipt"]["dispositions"][0]["priority"]=2;repin(a)
    elif kind=="closed":a["receipt"]["incident_clearance_established"]=True;repin(a)
    elif kind=="unknown_selection":a["receipt"]["dispositions"][0]["finding_id"]="0"*64;repin(a)
    elif kind=="missing_field":del a["receipt"]["dispositions"][0]["external_reference"];repin(a)
    elif kind=="coerced_bool":a["receipt"]["result_persisted"]=0;repin(a)
    elif kind=="reordered_evidence":doc["dossiers"].reverse()
    elif kind=="wrong_worklist":a["receipt"]["worklist_sha256"]="0"*64;repin(a)
    elif kind=="missing_confirmation":doc["confirmed"]=False
    elif kind=="empty":entries=[]
    else:entries=[a]*(reconciliation.MAX_RECEIPTS+1)
    with pytest.raises(ValueError):run(doc,*entries)


def test_cli_reconciles_real_evidence_exclusive_output(tmp_path):
    from server import agent_procurement_workforce_dossier as audit
    doc,work=fixture();entry=make_receipt(doc,work,work["findings"][0])
    mp,rp,out=[tmp_path/p for p in ("manifest.json","receipts.json","report.json")]
    audit.write_json(mp,doc["manifest"]);audit.write_json(rp,{"confirmed":True,"receipts":[entry]})
    paths=[]
    for i,d in enumerate(doc["dossiers"]):
        p=tmp_path/f"{i}.json";audit.write_json(p,d);paths.append(str(p))
    args=[str(mp),"--manifest-sha256",doc["expected_manifest_sha256"],"--receipts",str(rp),"--output",str(out),*paths]
    assert reconciliation.main(args)==0
    assert audit.read_json(out)==run(doc,entry)
    with pytest.raises(SystemExit):reconciliation.main(args)
