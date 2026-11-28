"""Real SQLite journal transactions and exact upstream monitor fixtures."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import sqlite3

import pytest

from server import agent_procurement_monitor_journal as journal
from shared.agent_procurement import digest
from test_agent_procurement_router_monitor import monitor, pilot, version_change, rehearsal, trial, study


@pytest.fixture
def local_db(tmp_path,monkeypatch):
    path=tmp_path/"journal.db"
    @contextmanager
    def connect():
        conn=sqlite3.connect(path,timeout=10)
        try:
            yield conn
            conn.commit()
        finally:conn.close()
    monkeypatch.setattr(journal.db,"connect",connect)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE workflow_knowledge(owner TEXT,workflow_id TEXT,revision INTEGER,payload TEXT)")
        conn.execute("INSERT INTO workflow_knowledge VALUES ('local','process_1',1,'{}')")
    return connect


def doc(initial,plan,snapshot,head=None):
    return {"initial":initial,"monitor_plan":plan,"snapshot":snapshot,
            "request":{"expected_head_sha256":head,"confirmed":True}}


def decision(view,disposition="refer_for_continued_monitoring_review"):
    return {"monitor_id":view["monitor_id"],"expected_head_sha256":view["head_sha256"],
            "disposition":disposition,"reviewer_alias":"reviewer-b","confirmed":True}


def test_persisted_checkpoint_chain_and_revision_atomic_decision(local_db,monitor):
    packet,initial,plan,final=monitor
    first=deepcopy(final);first["as_of_ms"]=2999;first["windows"].pop()
    a=journal.append(packet,"process_1",1,doc(initial,plan,first))
    assert a["result_persisted"] and a["checkpoint_count"]==1
    assert not a["eligible_for_continued_monitoring_review"]
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==a
    with pytest.raises(journal.JournalError,match="head changed"):
        journal.append(packet,"process_1",1,doc(initial,plan,final))
    b=journal.append(packet,"process_1",1,doc(initial,plan,final,a["head_sha256"]))
    assert b["eligible_for_continued_monitoring_review"]
    assert b["latest_report"]["prior_checkpoint_extension_verified"]
    assert b["history"][1]["previous_entry_sha256"]==a["head_sha256"]
    c=journal.decide("process_1",1,decision(b))
    assert c["entry_count"]==3 and c["checkpoint_count"]==2
    assert c["history"][-1]["details"]["disposition"]=="refer_for_continued_monitoring_review"
    assert not c["execution_authorized"]
    listed=journal.list_journals("process_1",1,{})
    assert listed["journals"][0]["head_sha256"]==c["head_sha256"]
    with local_db() as conn:conn.execute("INSERT INTO workflow_knowledge VALUES ('local','process_1',2,'{}')")
    with pytest.raises(journal.JournalError,match="latest"):
        journal.decide("process_1",1,decision(c,"hold"))
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==c
    assert journal.list_journals("process_1",2,{})["journals"]==[]


def test_rewrite_rejected_and_sticky_rollback_referral(local_db,monitor):
    packet,initial,plan,final=monitor
    first=deepcopy(final);first["as_of_ms"]=2999;first["windows"].pop()
    a=journal.append(packet,"process_1",1,doc(initial,plan,first))
    a=journal.decide("process_1",1,decision(a,"refer_for_rollback_review"))
    bad=deepcopy(final);bad["windows"][0]["observations"][0]["direct_cost_microunits"]+=1
    with pytest.raises(ValueError,match="removed or changed"):
        journal.append(packet,"process_1",1,doc(initial,plan,bad,a["head_sha256"]))
    b=journal.append(packet,"process_1",1,doc(initial,plan,final,a["head_sha256"]))
    assert b["latest_report"]["eligible_for_continued_monitoring_review"]
    assert b["rollback_referral_recorded"] and not b["eligible_for_continued_monitoring_review"]
    with pytest.raises(journal.JournalError,match="blocks continuation"):
        journal.decide("process_1",1,decision(b))
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==b


def test_concurrent_decisions_cannot_overwrite_and_forget_is_explicit(local_db,monitor):
    packet,initial,plan,final=monitor
    a=journal.append(packet,"process_1",1,doc(initial,plan,final))
    req=decision(a,"hold")
    def write():
        try:return journal.decide("process_1",1,req)
        except journal.JournalError as e:return e.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:write(),range(2)))
    assert sum(isinstance(x,dict) for x in results)==1
    assert 409 in results
    b=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert b["entry_count"]==2
    with pytest.raises(journal.JournalError):
        journal.forget("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":a["head_sha256"],"confirmed":True})
    r=journal.forget("process_1",1,{"monitor_id":"monitor-1","expected_head_sha256":b["head_sha256"],"confirmed":True})
    assert r["removed"] and not r["backup_erasure_verified"]
    assert journal.list_journals("process_1",1,{})["journals"]==[]
    with local_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM procurement_monitor_journal_entries").fetchone()[0]==0


def test_corrupt_chain_fails_closed(local_db,monitor):
    packet,initial,plan,final=monitor
    journal.append(packet,"process_1",1,doc(initial,plan,final))
    with local_db() as conn:
        conn.execute("UPDATE procurement_monitor_journal_entries SET entry_json='{}'")
    with pytest.raises(journal.JournalError,match="integrity"):
        journal.detail("process_1",1,{"monitor_id":"monitor-1"})


def test_capacity_failure_preserves_head(local_db,monitor,monkeypatch):
    packet,initial,plan,final=monitor
    a=journal.append(packet,"process_1",1,doc(initial,plan,final))
    monkeypatch.setattr(journal,"MAX_ENTRIES",1)
    with pytest.raises(journal.JournalError,match="capacity"):
        journal.decide("process_1",1,decision(a,"hold"))
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})["head_sha256"]==a["head_sha256"]


def test_sticky_adverse_signal_across_multiple_checkpoints(local_db,monitor):
    packet,initial,plan,final=monitor
    first=deepcopy(final);first["as_of_ms"]=2999;first["windows"].pop()
    first["windows"][0]["observations"][0]["critical_incident"]=True
    a=journal.append(packet,"process_1",1,doc(initial,plan,first))
    assert a["sticky_rollback_triggers"]
    final["windows"][0]["observations"][0]["critical_incident"]=True
    b=journal.append(packet,"process_1",1,doc(initial,plan,final,a["head_sha256"]))
    final["as_of_ms"]+=1
    c=journal.append(packet,"process_1",1,doc(initial,plan,final,b["head_sha256"]))
    assert c["sticky_rollback_triggers"]==a["sticky_rollback_triggers"] or all(x in c["sticky_rollback_triggers"] for x in a["sticky_rollback_triggers"])
    assert not c["eligible_for_continued_monitoring_review"]
