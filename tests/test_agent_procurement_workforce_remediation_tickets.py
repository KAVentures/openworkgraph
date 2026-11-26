"""Read-only Jira remediation binding, guarded reads and minimized history."""
from copy import deepcopy

import pytest

from server import agent_procurement_workforce_remediation_tickets as tickets
from server import agent_procurement_workforce_remediation as register
from server import agent_procurement_monitor_journal as journal
from server import db
from test_agent_procurement_monitor_journal import local_db
from test_agent_procurement_workforce_remediation import create,command,scope,forget_request

REF="owg:r:"+"a"*32
OTHER="owg:r:"+"b"*32
RECORD={"provider":"jira","resource_kind":"issue","host":"example.atlassian.net","resolver_locator":"ABC-123"}
NOW=1767225600000


def receipt(state="done"):
    return {"availability":"source_reported","provider":"jira","object_type":"issue",
            "verification_method":"explicit_opt_in_site_verified_oauth_jira_issue_status_read",
            "source_state_reported":True,"source_state":state,"verified_at":"2026-01-01T00:00:00+00:00",
            "audit_complete":True,"private_title":"DO NOT RETAIN",
            "verified_operational_outcome":{"kind":"jira_issue_resolved","status":"resolved",
                "verified":True,"scope":"jira_issue_only_not_customer_acknowledgement_or_refund",
                "source_timestamp":"2025-12-31T23:59:00+00:00"}}


def ticket_scope(v,t,f):
    return {**scope(v),"expected_revision":v["revision"],"expected_history_sha256":v["history_sha256"],
            "expected_ticket_revision":t["ticket_revision"],"expected_ticket_history_sha256":t["ticket_history_sha256"],
            "finding_id":f["finding_id"],"expected_finding_sha256":f["finding_sha256"]}


def setup(monkeypatch):
    _,v=create();f=v["findings"][0]
    v=register.change(command(v,f,"assigned"))
    monkeypatch.setattr(tickets.source,"resolve_resource_reference",lambda ref:deepcopy(RECORD) if ref in (REF,OTHER) else None)
    monkeypatch.setattr(tickets.source,"ensure_api_token",lambda:"test-local-secret")
    return v,f,tickets.status(scope(v))


def bind(v,f,t,ref=REF):
    return tickets.bind({**ticket_scope(v,t,f),"resource_ref":ref,
                         "confirm_operator_mapping":True,"confirm_local_retention":True})


def check(v,f,t):
    return tickets.check({**ticket_scope(v,t,f),"confirm_source_read":True})


def test_retained_ticket_reopening_and_removal(local_db,monkeypatch):
    v,f,t=setup(monkeypatch)
    calls=[]
    def read(ref,record,predicate):
        calls.append((ref,record,predicate));return receipt()
    monkeypatch.setattr(tickets.source,"_read_source",read)
    t=bind(v,f,t)
    assert not calls and t["ticket_revision"]==1
    old=t
    t=check(v,f,t)
    assert len(calls)==1 and t["ticket_revision"]==2
    # The receipt clock may be old; use an actual current source timestamp
    # for the subsequent refresh.
    from datetime import datetime,timezone
    def fresh_read(*args):
        r=receipt("done");r["verified_at"]=datetime.now(timezone.utc).isoformat();return r
    monkeypatch.setattr(tickets.source,"_read_source",fresh_read)
    t=check(v,f,t)
    assert t["bindings"][0]["latest_check"]["resolution_reported"]
    assert t["bindings"][0]["check_matches_current_register"]
    assert register.status(scope(v))==v
    assert not t["incident_clearance_established"]
    assert not t["external_write_performed"]
    with pytest.raises(journal.JournalError):check(v,f,old)
    v=register.change(command(v,f,"investigating"))
    assert not tickets.status(scope(v))["bindings"][0]["check_matches_current_register"]
    with db.connect() as conn:
        raw=conn.execute("SELECT ledger_json FROM procurement_audit_remediation_tickets").fetchone()[0]
        assert "ABC-123" not in raw and "example.atlassian.net" not in raw and "DO NOT RETAIN" not in raw
    with pytest.raises(journal.JournalError):register.forget(forget_request(v))
    assert register.forget({**forget_request(v),"expected_ticket_history_sha256":t["ticket_history_sha256"]})["removed"]
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_audit_remediation_tickets").fetchone()[0]==0


def test_changed_identity_or_revision_refuses_before_read(local_db,monkeypatch):
    v,f,t=setup(monkeypatch);t=bind(v,f,t)
    def forbidden(*args):pytest.fail("provider read must not happen")
    monkeypatch.setattr(tickets.source,"_read_source",forbidden)
    monkeypatch.setattr(tickets.source,"resolve_resource_reference",lambda ref:{**RECORD,"resolver_locator":"ABC-456"})
    with pytest.raises(ValueError):check(v,f,t)
    changed=register.change(command(v,f,"investigating"))
    with pytest.raises(journal.JournalError):check(v,f,t)
    assert tickets.status(scope(changed))["ticket_revision"]==1


def test_race_during_provider_read_discards_receipt(local_db,monkeypatch):
    v,f,t=setup(monkeypatch);t=bind(v,f,t)
    def race(*args):
        register.change(command(v,f,"investigating"));return receipt()
    monkeypatch.setattr(tickets.source,"_read_source",race)
    with pytest.raises(journal.JournalError):check(v,f,t)
    assert tickets.status(scope(v))["ticket_revision"]==1


@pytest.mark.parametrize("kind",["unknown","wrong_provider","no_confirmation","native_id","duplicate_identity"])
def test_mapping_validation(kind,local_db,monkeypatch):
    v,f,t=setup(monkeypatch)
    body={**ticket_scope(v,t,f),"resource_ref":REF,"confirm_operator_mapping":True,"confirm_local_retention":True}
    if kind=="unknown":body["resource_ref"]="owg:r:"+"c"*32
    elif kind=="wrong_provider":monkeypatch.setattr(tickets.source,"resolve_resource_reference",lambda ref:{**RECORD,"provider":"stripe"})
    elif kind=="no_confirmation":body["confirm_operator_mapping"]=False
    elif kind=="native_id":body["resource_ref"]="ABC-123"
    else:
        t=bind(v,f,t);other=v["findings"][1]
        v=register.change(command(v,other,"assigned"))
        body={**ticket_scope(v,t,other),"resource_ref":OTHER,"confirm_operator_mapping":True,"confirm_local_retention":True}
    with pytest.raises(ValueError):tickets.bind(body)


@pytest.mark.parametrize("state",["new","indeterminate","done"])
def test_projection_only_resolution_with_exact_scope(state):
    result=tickets.project(receipt(state),NOW)
    assert result["source_state"]==state
    assert result["resolution_reported"]==(state=="done")
    assert "private_title" not in result


@pytest.mark.parametrize("kind",["future","stale","wrong_scope","wrong_method","wrong_provider","unavailable","wrong_state","future_resolution"])
def test_projection_fail_closed(kind):
    r=receipt()
    if kind=="future":r["verified_at"]="2026-01-01T00:00:01+00:00"
    elif kind=="stale":r["verified_at"]="2025-12-31T23:00:00+00:00"
    elif kind=="wrong_scope":r["verified_operational_outcome"]["scope"]="business"
    elif kind=="wrong_method":r["verification_method"]="unknown"
    elif kind=="wrong_provider":r["provider"]="github"
    elif kind=="unavailable":r["availability"]="unknown"
    elif kind=="wrong_state":r["source_state"]="complete"
    else:r["verified_operational_outcome"]["source_timestamp"]="2026-01-01T00:01:00+00:00"
    assert not tickets.project(r,NOW)["resolution_reported"]
