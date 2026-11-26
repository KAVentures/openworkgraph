"""Exact appended audit history and explicitly selected owner continuity."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import agent_procurement_workforce_audit_review as workbench
from server import agent_procurement_workforce_remediation as register
from server import agent_procurement_monitor_journal as journal
from shared.agent_procurement import digest
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_exchange import row
from test_agent_procurement_workforce_timeline import snapshot,manifest
from test_agent_procurement_workforce_remediation import command,scope


def document(*snaps):
    m=manifest(*snaps)
    return {"confirmed":True,"expected_manifest_sha256":digest(m),
            "manifest":m,"dossiers":[d for d,_ in snaps]}


def setup_history(*,repeat_incident=False):
    a,b=Ed25519PrivateKey.generate(),Ed25519PrivateKey.generate()
    x=snapshot({"a":(a,[row("a",incident=True)]),"b":(b,[row("b")])},100)
    y=snapshot({"a":(a,[row("a")])},120)
    z=snapshot({"a":(a,[row("a",incident=repeat_incident)]),"b":(b,[row("b")])},140)
    before,after=document(x,y),document(x,y,z)
    work=workbench.review_timeline(before)["worklist"]
    v=workbench.create_remediation({**before,"expected_worklist_sha256":work["worklist_sha256"],
                                    "confirm_local_retention":True})
    finding=next(f for f in v["findings"] if f["kind"]=="rollback_referral")
    for state,ref in (("assigned",None),("investigating",None),("follow_up_reported","CASE-1")):
        v=register.change(command(v,finding,state,ref=ref))
    current=workbench.review_timeline(after)["worklist"]
    request={"previous_worklist_sha256":v["worklist_sha256"],
             "expected_previous_revision":v["revision"],
             "expected_previous_history_sha256":v["history_sha256"],
             "previous_manifest_sha256":before["expected_manifest_sha256"],
             "expected_current_worklist_sha256":current["worklist_sha256"],"confirmed":True}
    return before,after,v,finding,request


def preview(after,request):
    return workbench.preview_continuity({**after,"continuity":request})


def carry(after,request,comparison,ids):
    return workbench.carry_continuity({**after,"continuity":{**request,
        "expected_continuity_sha256":comparison["continuity_sha256"],
        "reviewer_alias":"reviewer","carry_finding_ids":ids,"confirm_local_retention":True}})


def test_same_pin_owner_only_prior_history_preserved(local_db):
    _,after,prior,f,req=setup_history()
    p=preview(after,req)
    match=next(r for r in p["findings"] if r["finding_id"]==f["finding_id"])
    assert match["relation"]=="same_evidence_pin" and match["owner_carry_eligible"]
    assert match["previous_state"]=="follow_up_reported"
    assert any(r["kind"]=="journal_absent_latest" and r["relation"]=="not_in_successor" for r in p["findings"])
    assert p["verified_prefix_snapshot_count"]==2
    assert p["continuity_sha256"]==digest({k:v for k,v in p.items() if k!="continuity_sha256"})
    assert not p["progress_carried"] and not p["incident_clearance_established"]
    successor=carry(after,req,p,[f["finding_id"]])
    assert successor["revision"]==1
    row_after=next(r for r in successor["findings"] if r["finding_id"]==f["finding_id"])
    assert row_after["state"]=="assigned" and row_after["owner_alias"]=="ops"
    assert row_after["evidence_reference"] is None
    assert successor["lineage"]["previous_history_sha256"]==prior["history_sha256"]
    assert successor["lineage"]["carried_owner_finding_ids"]==[f["finding_id"]]
    assert register.status(scope(prior))==prior
    assert register.status(scope(successor))==successor
    assert not successor["incident_clearance_established"]
    with pytest.raises(journal.JournalError):carry(after,req,p,[f["finding_id"]])


def test_changed_incident_pin_cannot_carry_owner(local_db):
    _,after,prior,f,req=setup_history(repeat_incident=True)
    p=preview(after,req)
    changed=next(r for r in p["findings"] if r["finding_id"]==f["finding_id"])
    assert changed["relation"]=="changed_evidence_pin"
    assert not changed["owner_carry_eligible"]
    with pytest.raises(ValueError):carry(after,req,p,[f["finding_id"]])
    successor=carry(after,req,p,[])
    assert successor["revision"]==0 and successor["rollback_referrals_retained"]==1
    assert register.status(scope(prior))==prior


@pytest.mark.parametrize("kind",["wrong_prior_manifest","wrong_current_pin","same_worklist","wrong_prior_revision",
                                  "wrong_history","wrong_continuity","duplicate_carry","unknown_carry","no_confirmation",
                                  "changed_prior_after_preview","reordered_evidence"])
def test_continuity_rejects_stale_or_unrelated_history(kind,local_db):
    before,after,prior,f,req=setup_history()
    p=preview(after,req);ids=[f["finding_id"]]
    if kind=="wrong_prior_manifest":req["previous_manifest_sha256"]="0"*64
    elif kind=="wrong_current_pin":req["expected_current_worklist_sha256"]="0"*64
    elif kind=="same_worklist":after=before;req["expected_current_worklist_sha256"]=prior["worklist_sha256"]
    elif kind=="wrong_prior_revision":req["expected_previous_revision"]+=1
    elif kind=="wrong_history":req["expected_previous_history_sha256"]="0"*64
    elif kind=="wrong_continuity":p["continuity_sha256"]="0"*64
    elif kind=="duplicate_carry":ids*=2
    elif kind=="unknown_carry":ids=["0"*64]
    elif kind=="no_confirmation":req["confirmed"]=False
    elif kind=="changed_prior_after_preview":prior=register.change(command(prior,f,"investigating"))
    else:after["dossiers"].reverse()
    with pytest.raises((ValueError,journal.JournalError)):carry(after,req,p,ids)
    assert len(register.list_registers({})["registers"])==1
    assert register.status(scope(prior))==prior


def test_concurrent_successor_creation_never_overwrites(local_db):
    _,after,prior,f,req=setup_history()
    p=preview(after,req)
    def attempt(_):
        try:return carry(after,req,p,[f["finding_id"]])["revision"]
        except journal.JournalError as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==[1,409]
    assert len(register.list_registers({})["registers"])==2


def test_unrelated_valid_manifest_refused(local_db):
    _,after,prior,f,req=setup_history()
    key=Ed25519PrivateKey.generate()
    unrelated=document(snapshot({"z":(key,[row("z",incident=True)])},100),
                       snapshot({"z":(key,[row("z")])},120),
                       snapshot({"z":(key,[row("z")])},140))
    req["expected_current_worklist_sha256"]=workbench.review_timeline(unrelated)["worklist"]["worklist_sha256"]
    with pytest.raises(ValueError):preview(unrelated,req)
