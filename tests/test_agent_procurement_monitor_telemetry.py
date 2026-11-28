"""Scoped producer submissions against real local SQLite and frozen monitor plans."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

import pytest

from server import agent_procurement_monitor_journal as journal
from server import agent_procurement_monitor_telemetry as telemetry
from test_agent_procurement_monitor_journal import local_db, doc, monitor, pilot, version_change, rehearsal, trial, study
from shared.agent_procurement import digest


@pytest.fixture
def producer(local_db,monitor,monkeypatch):
    packet,initial,plan,final=monitor
    clock=[2000]
    monkeypatch.setattr(telemetry,"_now",lambda:clock[0])
    empty=deepcopy(final);empty["as_of_ms"]=2000;empty["windows"]=[]
    head=journal.append(packet,"process_1",1,doc(initial,plan,empty))
    grant=telemetry.grant("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":head["head_sha256"],
        "producer_alias":"adapter-a","window_id":"w1","arm":"challenger","ttl_seconds":60,"confirmed":True})
    slots={s["slot_id"]:s for w in plan["windows"] if w["window_id"]=="w1" for s in w["plan"]["slots"]}
    batch={"format":"openworkgraph.procurement-monitor-producer-batch.v1","batch_id":"batch-1",
        "observations":[deepcopy(o) for o in final["windows"][0]["observations"] if slots[o["slot_id"]]["arm"]=="challenger"]}
    clock[0]=3001
    return grant,batch,clock,final


def credential(grant):
    return telemetry.authenticate("Bearer "+grant["producer_token"])


def test_scoped_intake_updates_report_and_exact_replay(producer,local_db):
    grant,batch,clock,_=producer
    cred=credential(grant)
    receipt=telemetry.ingest(cred,batch)
    assert receipt["accepted_observations"]==len(batch["observations"])
    assert receipt["source_credential_authenticated"] and not receipt["business_outcomes_independently_verified"]
    assert "head_sha256" not in receipt and "report" not in receipt
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert detail["credential_authenticated_observations"]==len(batch["observations"])
    assert detail["checkpoint_count"]==2
    assert detail["latest_report"]["recorded_cases"]==len(batch["observations"])
    assert all(detail["producer_attribution"][o["slot_id"]]["observation_sha256"]==digest(o) for o in batch["observations"])
    assert telemetry.ingest(cred,{**batch,"observations":list(reversed(batch["observations"]))})["idempotent_replay"]
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==detail
    assert grant["producer_token"] not in str(detail)
    with local_db() as conn:
        token_hash=conn.execute("SELECT token_hash FROM procurement_monitor_producers").fetchone()[0]
        assert token_hash==telemetry._token_hash(grant["producer_token"])
    changed=deepcopy(batch);changed["observations"][0]["direct_cost_microunits"]+=1
    with pytest.raises(journal.JournalError,match="Batch ID"):
        telemetry.ingest(cred,changed)


def test_outside_arm_or_window_and_duplicate_slot_are_refused(producer):
    grant,batch,clock,final=producer
    cred=credential(grant)
    ids={o["slot_id"] for o in batch["observations"]}
    for o in [next(o for o in final["windows"][0]["observations"] if o["slot_id"] not in ids),final["windows"][1]["observations"][0]]:
        with pytest.raises(journal.JournalError,match="outside producer scope"):
            telemetry.ingest(cred,{**batch,"observations":[o]})
    telemetry.ingest(cred,batch)
    clock[0]+=1
    with pytest.raises(journal.JournalError,match="already retained"):
        telemetry.ingest(cred,{**batch,"batch_id":"batch-2"})


def test_wrong_version_retained_as_adverse_not_discarded(producer):
    grant,batch,clock,_=producer
    batch["observations"][0]["actual_router_configuration_sha256"]="f"*64
    telemetry.ingest(credential(grant),batch)
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert detail["sticky_rollback_triggers"]
    assert not detail["eligible_for_continued_monitoring_review"]


def test_revoke_expiry_and_removal(producer,local_db):
    grant,batch,clock,_=producer
    cred=credential(grant)
    clock[0]=grant["grant"]["expires_ms"]
    with pytest.raises(journal.JournalError) as e:telemetry.ingest(cred,batch)
    assert e.value.status==401
    clock[0]=3001
    revoked=telemetry.revoke("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":grant["head_sha256"],
        "grant_id":grant["grant"]["grant_id"],"confirmed":True})
    with pytest.raises(journal.JournalError) as e:telemetry.ingest(cred,batch)
    assert e.value.status==401
    journal.forget("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":revoked["head_sha256"],"confirmed":True})
    with local_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_monitor_producers").fetchone()[0]==0


def test_concurrent_exact_batch_is_idempotent(producer):
    grant,batch,_,_=producer
    cred=credential(grant)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:telemetry.ingest(cred,batch),range(4)))
    assert sum(not r["idempotent_replay"] for r in results)==1
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert detail["checkpoint_count"]==2


def test_stale_revision_and_unrecognized_credentials(producer,local_db):
    grant,batch,_,_=producer
    cred=credential(grant)
    with local_db() as conn:conn.execute("INSERT INTO workflow_knowledge VALUES ('local','process_1',2,'{}')")
    with pytest.raises(journal.JournalError,match="latest"):
        telemetry.ingest(cred,batch)
    for header in (None,"Bearer ordinary-api-token","OWG-Session "+grant["producer_token"],
                   "Bearer "+grant["producer_token"][:-1]+("a" if grant["producer_token"][-1]!="a" else "b")):
        with pytest.raises(journal.JournalError) as e:telemetry.authenticate(header)
        assert e.value.status==401


def test_clock_refresh_detects_overdue_without_new_producer_evidence(producer,monkeypatch):
    grant,batch,clock,_=producer
    telemetry.ingest(credential(grant),batch)
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    monkeypatch.setattr(journal.time,"time",lambda:4.2)
    result=journal.refresh("process_1",1,{"monitor_id":"monitor-1",
        "expected_head_sha256":detail["head_sha256"],"confirmed":True})
    assert result["latest_report"]["as_of_ms"]==4200
    assert result["latest_report"]["recorded_cases"]==len(batch["observations"])
    assert result["history"][-1]["kind"]=="clock_checkpoint"
    assert result["credential_authenticated_observations"]==len(batch["observations"])
    assert result["overdue_window_history"]==["w1","w2"]
    assert not result["eligible_for_continued_monitoring_review"]


def test_outstanding_grants_reserve_revocation_entries(producer,monkeypatch):
    grant,batch,clock,_=producer
    # Current history is first checkpoint plus grant, with one outstanding revoke.
    monkeypatch.setattr(journal,"MAX_ENTRIES",3)
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert detail["reserved_disable_entries"]==1
    assert detail["ordinary_entries_remaining"]==0
    with pytest.raises(journal.JournalError,match="capacity reserved"):
        telemetry.ingest(credential(grant),batch)
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==detail
    revoked=telemetry.revoke("process_1",1,{"monitor_id":"monitor-1","grant_id":grant["grant"]["grant_id"],
        "expected_head_sha256":detail["head_sha256"],"confirmed":True})
    final=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert revoked["revoked"] and final["entry_count"]==3 and final["reserved_disable_entries"]==0
    with pytest.raises(journal.JournalError):
        credential(grant)


def test_failed_grant_reservation_rolls_back_credential(producer,local_db,monkeypatch):
    grant,batch,clock,_=producer
    monkeypatch.setattr(journal,"MAX_ENTRIES",4)
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    with pytest.raises(journal.JournalError,match="capacity reserved"):
        telemetry.grant("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":detail["head_sha256"],
            "producer_alias":"adapter-b","window_id":"w1","arm":"incumbent","ttl_seconds":60,"confirmed":True})
    with local_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_monitor_producers").fetchone()[0]==1
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==detail


def test_operator_manifest_is_exact_scope_and_no_secret(producer):
    grant,batch,clock,final=producer
    req={"monitor_id":"monitor-1","expected_head_sha256":grant["head_sha256"],
         "grant_id":grant["grant"]["grant_id"],"confirmed":True}
    value=telemetry.manifest("process_1",1,req)
    assert {s["slot_id"] for s in value["slots"]}=={o["slot_id"] for o in batch["observations"]}
    assert all(not s["already_recorded"] for s in value["slots"])
    assert grant["producer_token"] not in str(value)
    assert all(set(s)=={"slot_id","case_token_sha256","expected_router_configuration_sha256","already_recorded"} for s in value["slots"])
    assert not value["credential_included"] and not value["execution_authorized"]
    assert value["assignment_sha256"]==digest({k:v for k,v in value.items() if k!="assignment_sha256"})
    telemetry.ingest(credential(grant),batch)
    with pytest.raises(journal.JournalError,match="head changed"):
        telemetry.manifest("process_1",1,req)
    detail=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    req["expected_head_sha256"]=detail["head_sha256"]
    assert all(s["already_recorded"] for s in telemetry.manifest("process_1",1,req)["slots"])
    clock[0]=grant["grant"]["expires_ms"]
    with pytest.raises(journal.JournalError,match="Active scoped"):
        telemetry.manifest("process_1",1,req)


def test_native_client_to_retained_server_exact_replay(producer):
    import httpx
    import json
    from shared.agent_procurement_monitor_protocol import Assignment
    from shared import agent_procurement_monitor_client as native
    grant,batch,clock,_=producer
    req={"monitor_id":"monitor-1","expected_head_sha256":grant["head_sha256"],
         "grant_id":grant["grant"]["grant_id"],"confirmed":True}
    assignment=Assignment.model_validate(telemetry.manifest("process_1",1,req))
    prepared=native.prepare(assignment,batch["observations"],now=clock[0])
    assert len(prepared)==1
    def handler(request):
        assert request.url.path==assignment.ingest_path
        cred=telemetry.authenticate(request.headers["authorization"])
        result=telemetry.ingest(cred,json.loads(request.content))
        return httpx.Response(200,json=result)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        first=native.submit(assignment,prepared[0],"http://127.0.0.1:8000",
            grant["producer_token"],client=transport,now=clock[0])
        retained=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
        replay=native.submit(assignment,prepared[0],"http://127.0.0.1:8000",
            grant["producer_token"],client=transport,now=clock[0])
    assert not first["idempotent_replay"] and replay["idempotent_replay"]
    assert retained==journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert retained["credential_authenticated_observations"]==len(batch["observations"])
