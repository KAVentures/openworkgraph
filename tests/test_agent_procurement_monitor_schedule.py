from copy import deepcopy
from itertools import product

import pytest

from server import agent_procurement_monitor_schedule as schedule
from server import agent_procurement_monitor_journal as journal
from server import local_scheduler
from test_agent_procurement_monitor_journal import local_db, doc, monitor, pilot, version_change, rehearsal, trial, study


def consent(view,enabled=True):
    return {"monitor_id":view["monitor_id"],"expected_head_sha256":view["head_sha256"],
            "enabled":enabled,"confirmed":True}


def test_crossed_boundaries_against_independent_oracle():
    for delay,old,now in product(range(4),range(14),range(14)):
        plan={"request":{"maximum_reporting_delay_ms":delay},"windows":[
            {"window_id":"a","plan":{"request":{"window_start_ms":2,"window_end_exclusive_ms":5}}},
            {"window_id":"b","plan":{"request":{"window_start_ms":5,"window_end_exclusive_ms":8}}}]}
        expected={(w,phase,t) for w,start,end in (("a",2,5),("b",5,8))
                  for phase,t in (("start",start),("close",end),("reporting_deadline",end+delay)) if old<t<=now}
        result=schedule.crossed_deadlines(plan,old,now)
        assert {(x["window_id"],x["phase"],x["at_ms"]) for x in result}==expected
        assert result==sorted(result,key=lambda x:(x["at_ms"],x["window_id"],x["phase"]))


def test_opt_in_and_no_idle_writes(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    snapshot=deepcopy(snapshot);snapshot["as_of_ms"]=2000;snapshot["windows"]=[]
    head=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    assert schedule.run_once(3200)["not_enabled"]==1
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==head
    a=schedule.configure("process_1",1,consent(head))
    assert a["scheduled_deadline_review_enabled"]
    assert schedule.run_once(2500)["idle"]==1
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==a
    assert schedule.run_once(3200)["advanced"]==1
    b=journal.detail("process_1",1,{"monitor_id":"monitor-1"})
    assert b["history"][-1]["kind"]=="scheduled_checkpoint"
    assert b["overdue_window_history"]==["w1"]
    assert b["scheduled_deadline_review_enabled"]
    assert {x["phase"] for x in b["history"][-1]["details"]["crossed_deadlines"]}=={"start","close","reporting_deadline"}
    assert schedule.run_once(3300)["idle"]==1
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==b
    c=schedule.configure("process_1",1,consent(b,False))
    assert schedule.run_once(4500)["not_enabled"]==1
    assert journal.detail("process_1",1,{"monitor_id":"monitor-1"})==c


def test_historical_revision_and_stale_consent(local_db,monitor):
    packet,initial,plan,snapshot=monitor
    snapshot=deepcopy(snapshot);snapshot["as_of_ms"]=2000;snapshot["windows"]=[]
    head=journal.append(packet,"process_1",1,doc(initial,plan,snapshot))
    a=schedule.configure("process_1",1,consent(head))
    with pytest.raises(journal.JournalError,match="head changed"):
        schedule.configure("process_1",1,consent(head,False))
    with local_db() as conn:conn.execute("INSERT INTO workflow_knowledge VALUES ('local','process_1',2,'{}')")
    assert schedule.run_once(4500)["historical"]==1
    with pytest.raises(journal.JournalError,match="latest"):
        schedule.configure("process_1",1,consent(a))
    b=schedule.configure("process_1",1,consent(a,False))
    assert not b["scheduled_deadline_review_enabled"]


def test_existing_scheduler_dispatches_only_local_target(tmp_path,monkeypatch):
    monkeypatch.setattr(local_scheduler,"DATA_DIR",tmp_path)
    monkeypatch.setattr(schedule,"run_once",lambda:{"advanced":2,"failed":0})
    with pytest.raises(local_scheduler.ScheduleError):
        local_scheduler.create(schedule.KIND,"other",interval_minutes=15,confirm_scheduled_runs=True)
    job=local_scheduler.create(schedule.KIND,"local",interval_minutes=15,confirm_scheduled_runs=True)
    assert local_scheduler.run_due(now=10**10)==[{"schedule_id":job["schedule_id"],"outcome":"succeeded:monitor_deadline_review_2_advanced"}]
    monkeypatch.setattr(schedule,"run_once",lambda:{"advanced":1,"failed":1})
    assert schedule.run_scheduled("local")==("failed:monitor_deadline_review_incomplete",0.0)
