"""Persistent local remediation of independently recomputed audit findings."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json

import pytest

from server import agent_procurement_workforce_remediation as remediation
from server import agent_procurement_workforce_audit_review as workbench
from server import agent_procurement_monitor_journal as journal
from server import db
from shared.agent_procurement import digest
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_audit_triage import evidence


def create():
    doc=evidence()
    work=workbench.review_timeline(doc)["worklist"]
    req={**doc,"expected_worklist_sha256":work["worklist_sha256"],"confirm_local_retention":True}
    return req,workbench.create_remediation(req)


def command(view,row,state,*,owner="ops",ref=None):
    return {"worklist_sha256":view["worklist_sha256"],"expected_revision":view["revision"],
            "expected_history_sha256":view["history_sha256"],"finding_id":row["finding_id"],
            "expected_finding_sha256":row["finding_sha256"],"state":state,"owner_alias":owner,
            "reviewer_alias":"reviewer","evidence_reference":ref,"confirmed":True}


def scope(view):
    return {"worklist_sha256":view["worklist_sha256"]}


def forget_request(view):
    return {**scope(view),"expected_revision":view["revision"],
            "expected_history_sha256":view["history_sha256"],"confirmed_exported":True}


def test_persistent_progress_reopen_and_minimized_storage(local_db):
    req,v=create();row=v["findings"][0];initial=v
    assert row["priority"]==0 and row["state"]=="unassigned"
    for state,ref in (("assigned",None),("investigating",None),("awaiting_evidence",None),
                      ("investigating",None),("follow_up_reported","CASE-1"),("investigating",None)):
        before=v
        v=remediation.change(command(v,row,state,ref=ref))
        assert v["revision"]==before["revision"]+1
        assert v["events"][-1]["previous_sha256"]==before["history_sha256"]
        assert v["events"][-1]["event_sha256"]==v["history_sha256"]
        assert remediation.status(scope(v))==v
        assert v["rollback_referrals_retained"]==initial["rollback_referrals_retained"]
        assert not v["incident_clearance_established"] and not v["work_completed_verified"]
    assert workbench.create_remediation(req)==v
    listed=remediation.list_registers({})["registers"]
    assert len(listed)==1 and listed[0]["revision"]==6
    with db.connect() as conn:
        raw=conn.execute("SELECT register_json FROM procurement_audit_remediation").fetchone()[0]
        for forbidden in ("case_tags","source_tags","public_key_hex","signature_hex","packets","dispositions"):
            assert forbidden not in raw
    with pytest.raises(journal.JournalError):remediation.forget(forget_request(initial))
    assert remediation.forget(forget_request(v))["removed"]
    assert remediation.list_registers({})["registers"]==[]
    with pytest.raises(journal.JournalError):remediation.status(scope(v))


@pytest.mark.parametrize("kind",["wrong_pin","wrong_revision","wrong_history","wrong_finding",
    "skip_assignment","fake_closure","missing_confirmation","coerced_confirmation"])
def test_fail_closed_and_no_write(kind,local_db):
    _,v=create();row=v["findings"][0];cmd=command(v,row,"assigned")
    if kind=="wrong_pin":cmd["expected_finding_sha256"]="0"*64
    elif kind=="wrong_revision":cmd["expected_revision"]=1
    elif kind=="wrong_history":cmd["expected_history_sha256"]="0"*64
    elif kind=="wrong_finding":cmd["finding_id"]="0"*64
    elif kind=="skip_assignment":cmd["state"]="investigating"
    elif kind=="fake_closure":cmd["state"]="closed"
    elif kind=="missing_confirmation":cmd["confirmed"]=False
    else:cmd["confirmed"]="true"
    with pytest.raises((ValueError,journal.JournalError)):remediation.change(cmd)
    assert remediation.status(scope(v))==v


def test_reassignment_and_evidence_reference_required(local_db):
    _,v=create();row=v["findings"][0]
    v=remediation.change(command(v,row,"assigned"))
    with pytest.raises(ValueError):remediation.change(command(v,row,"assigned"))
    with pytest.raises(ValueError):remediation.change(command(v,row,"investigating",owner="other"))
    v=remediation.change(command(v,row,"assigned",owner="other"))
    v=remediation.change(command(v,row,"investigating",owner="other"))
    with pytest.raises(ValueError):remediation.change(command(v,row,"follow_up_reported",owner="other"))
    with pytest.raises(ValueError):remediation.change(command(v,row,"follow_up_reported",owner="other",ref="CASE",),now=0)
    v=remediation.change(command(v,row,"follow_up_reported",owner="other",ref="CASE"))
    assert v["findings"][0]["state"]=="follow_up_reported"
    assert v["findings"][0]["evidence_reference"]=="CASE"


def test_concurrent_exact_revision_only_one_wins(local_db):
    _,v=create()
    cmds=[command(v,r,"assigned") for r in v["findings"][:2]]
    def attempt(cmd):
        try:return remediation.change(cmd)["revision"]
        except journal.JournalError as exc:return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,cmds))==[1,409]
    assert remediation.status(scope(v))["revision"]==1


@pytest.mark.parametrize("kind",["pin","consent","capacity","tampered_source"])
def test_retention_recomputes_source_and_requires_consent(kind,local_db,monkeypatch):
    doc=evidence();w=workbench.review_timeline(doc)["worklist"]
    req={**doc,"expected_worklist_sha256":w["worklist_sha256"],"confirm_local_retention":True}
    if kind=="pin":req["expected_worklist_sha256"]="0"*64
    elif kind=="consent":req["confirm_local_retention"]=False
    elif kind=="capacity":monkeypatch.setattr(remediation,"MAX_FINDINGS",0)
    else:req["dossiers"][0]["report"]["included_journals"]=999
    with pytest.raises(ValueError):workbench.create_remediation(req)
    assert remediation.list_registers({})["registers"]==[]


def test_history_corruption_rejected_even_with_rehashed_invalid_transition(local_db):
    _,v=create();row=v["findings"][0]
    v=remediation.change(command(v,row,"assigned"))
    with db.connect() as conn:
        raw=json.loads(conn.execute("SELECT register_json FROM procurement_audit_remediation").fetchone()[0])
        event=raw["events"][0];event["state"]="follow_up_reported"
        event["event_sha256"]=digest({k:x for k,x in event.items() if k!="event_sha256"})
        raw["history_sha256"]=event["event_sha256"]
        conn.execute("UPDATE procurement_audit_remediation SET register_json=?",(json.dumps(raw),))
    with pytest.raises(ValueError):remediation.status(scope(v))


def test_history_capacity_and_removal_consent(local_db,monkeypatch):
    _,v=create();row=v["findings"][0]
    monkeypatch.setattr(remediation,"MAX_EVENTS",0)
    with pytest.raises(journal.JournalError):remediation.change(command(v,row,"assigned"))
    with pytest.raises(ValueError):remediation.forget({**forget_request(v),"confirmed_exported":False})
    assert remediation.status(scope(v))==v
