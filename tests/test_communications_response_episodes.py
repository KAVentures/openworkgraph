"""Response episode extraction and backlog conservation over synthetic exports."""
from datetime import datetime,timedelta,timezone
from collections import Counter
import json
import random

import pytest

from shared import communications_response_episodes as e
from shared import communications_mining as cm

BASE=datetime(2024,1,1,tzinfo=timezone.utc)

def at(minutes):
    return BASE+timedelta(minutes=minutes)

def item(minutes,direction="inbound",label="Cancellation"):
    return (at(minutes) if minutes is not None else None,direction,label)

def inspect(threads,cutoff=300,deadlines=(60,120),unthreaded=0):
    return e.analyze(threads,cutoff=at(cutoff),deadlines=list(deadlines),unthreaded_messages=unthreaded)


def test_episode_partition_and_deadline_boundary():
    threads={
        "a":[item(-20,"outbound"),item(0),item(5),item(60,"outbound"),
             item(65,"outbound"),item(90),item(100),item(150,"outbound"),item(240)],
        "b":[item(10),item(140,"outbound")],
        "c":[item(180)],
        "d":[item(250),item(310,"outbound")],
    }
    r=inspect(threads);s=r["overall"];w=r["workload"]
    assert s["episodes"]==6 and s["first_observed_episodes"]==4 and s["follow_up_episodes"]==2
    assert s["inbound_messages"]==8 and s["episodes_with_repeat_inbound"]==2
    assert s["additional_inbound_before_observed_reply_or_cutoff"]==2
    assert s["observed_reply_episodes"]==3 and s["without_observed_reply"]==3
    assert s["observed_reply_seconds"]=={"median":3600.0,"p90_nearest_rank":7800.0,"count":3}
    assert s["deadlines"][0]["counts"]==dict(zip(e.STATES,[2,1,1,2]))
    assert s["deadlines"][1]["counts"]==dict(zip(e.STATES,[2,1,0,3]))
    assert s["deadlines"][0]["observed_within_deadline_share"]==pytest.approx(2/6)
    assert s["deadlines"][0]["upper_share_if_pending_reply_on_time"]==pytest.approx(4/6)
    assert w["overall"]["new_episodes"]==6 and w["overall"]["observed_replies"]==3
    assert w["overall"]["closing_observed_backlog"]==3 and w["overall"]["peak_observed_backlog"]==3
    assert w["overall"]["episode_wait_seconds"]==480*60
    assert w["overall"]["time_weighted_observed_backlog"]==pytest.approx(480/300)
    assert w["open_deadline_exposure"][0]["past_deadline"]==1
    assert w["open_deadline_exposure"][0]["due_within_next_hour"]==2
    assert r["coverage"]["dated_messages_after_cutoff_in_fully_dated_threads"]==1
    assert not r["production_sla_verified"]


def test_exclusion_precedence_and_no_future_reply():
    threads={"undated":[item(None),item(0)],
             "unknown":[item(0,""),item(1)],
             "tie":[item(0),item(0,"outbound")],
             "outbound":[item(1,"outbound")],
             "future":[item(301)],
             "good":[item(0),item(301,"outbound")],
             "unknown_future":[item(0),item(301,"")]}
    r=inspect(threads,unthreaded=4)
    c=r["coverage"]
    assert c["threaded_population"]==7 and c["eligible_threads"]==2 and c["excluded_threads"]==5
    assert c["exclusive_exclusion_reasons"]=={"mixed_direction_timestamp_tie":1,"no_observed_inbound":1,
       "only_after_cutoff":1,"undated_message":1,"unknown_direction":1}
    assert c["unthreaded_messages"]==4 and c["dated_messages_after_cutoff_in_fully_dated_threads"]==3
    assert r["overall"]["without_observed_reply"]==2
    assert r["overall"]["deadlines"][0]["counts"]["no_reply_deadline_elapsed"]==2


def test_same_direction_tie_is_deterministic_and_mixed_label_not_arbitrary():
    rows=[item(0,label="A"),item(0,label="B"),item(1,"outbound")]
    threads={str(i):list(rows) for i in range(5)}
    a=inspect(threads)
    b=inspect({k:list(reversed(v)) for k,v in reversed(list(threads.items()))})
    assert a==b
    g=a["by_first_inbound_intent"]["rows"][0]
    assert g["group_kind"]=="mixed_first_timestamp" and g["label"] is None
    assert g["distinct_threads"]==5 and g["episodes_with_repeat_inbound"]==5


def test_minimum_distinct_threads_not_episode_count_and_no_identifiers():
    rare=[item(2*i,label="PRIVATE_INTENT") if j==0 else item(2*i+1,"outbound")
          for i in range(20) for j in range(2)]
    threads={"PRIVATE_THREAD":rare}
    threads.update({f"thread-{i}":[item(0,label="Common"),item(60,"outbound")] for i in range(5)})
    r=inspect(threads)
    assert r["overall"]["episodes"]==25
    g=r["by_first_inbound_intent"]
    assert g["suppressed_or_limited_groups"]==1 and g["episodes_in_suppressed_or_limited_groups"]==20
    assert [x["label"] for x in g["rows"]]==["Common"]
    assert "PRIVATE_INTENT" not in json.dumps(r) and "PRIVATE_THREAD" not in json.dumps(r)


def test_weekly_balance_inclusive_cutoff_and_carry_in():
    week=7*24*60
    threads={str(i):[item(0),item(week,"outbound"),item(week+60)] for i in range(5)}
    r=inspect(threads,cutoff=2*week,deadlines=(60,1440))
    windows=r["workload"]["weekly_windows"]
    assert len(windows)==3
    assert [x["new_episodes"] for x in windows]==[5,5,0]
    assert [x["observed_replies"] for x in windows]==[0,5,0]
    assert [x["opening_observed_backlog"] for x in windows]==[0,5,5]
    assert [x["closing_observed_backlog"] for x in windows]==[5,5,5]
    assert sum(x["episode_wait_seconds"] for x in windows)==r["workload"]["overall"]["episode_wait_seconds"]
    assert windows[-1]["time_weighted_observed_backlog"] is None
    assert windows[-1]["partial_week"]
    long=inspect({str(i):[item(0)] for i in range(5)},cutoff=40*week+60)
    w=long["workload"]
    assert w["older_weeks_not_displayed"] and len(w["weekly_windows"])==26
    assert w["weekly_windows"][0]["opening_observed_backlog"]==5
    assert w["overall"]["episode_wait_seconds"]==5*(40*week+60)*60


def test_simultaneous_cross_thread_replies_and_arrivals_are_netted():
    r=inspect({"a":[item(0),item(60,"outbound")],"b":[item(60),item(120,"outbound")]},cutoff=120)
    w=r["workload"]["overall"]
    assert w["peak_observed_backlog"]==1
    assert w["closing_observed_backlog"]==0 and w["episode_wait_seconds"]==7200
    assert w["time_weighted_observed_backlog"]==1


def test_empty_eligible_population_has_no_implied_backlog():
    r=inspect({"x":[item(None)]})
    assert r["overall"]["episodes"]==0
    assert r["overall"]["deadlines"][0]["observed_within_deadline_share"] is None
    assert not r["workload"]["available"]


def test_randomized_episode_and_backlog_oracle():
    rng=random.Random(22207)
    for _ in range(160):
        cutoff=40
        threads={}
        expected=[]
        for i in range(rng.randint(1,12)):
            stamps=sorted(rng.sample(range(46),rng.randint(1,15)))
            items=[item(t,rng.choice(["inbound","outbound"])) for t in stamps]
            threads[str(i)]=list(reversed(items))
            # Independent partition: each observed outbound partitions
            # earlier inbound contacts; a last interval extends to cutoff.
            inbound=[t for t,d,_ in items if d=="inbound" and t<=at(cutoff)]
            outbound=[t for t,d,_ in items if d=="outbound" and t<=at(cutoff)]
            last=BASE-timedelta(days=1)
            for right in outbound+[None]:
                subset=[t for t in inbound if last<t and (right is None or t<right)]
                if subset:expected.append((subset[0],right,len(subset)))
                if right is not None:last=right
        r=inspect(threads,cutoff=cutoff,deadlines=(1,5,20))
        s=r["overall"]
        assert s["episodes"]==len(expected)
        assert s["inbound_messages"]==sum(n for _,_,n in expected)
        assert s["observed_reply_episodes"]==sum(b is not None for _,b,_ in expected)
        if not expected:
            assert not r["workload"]["available"];continue
        w=r["workload"]["overall"]
        # Exact minute-grid oracle: intervals are piecewise constant
        # on [minute,minute+1), plus a final instantaneous cutoff count.
        counts=[sum(a<=at(t) and (b is None or b>at(t)) for a,b,_ in expected) for t in range(cutoff+1)]
        assert w["peak_observed_backlog"]==max(counts)
        assert w["closing_observed_backlog"]==counts[-1]
        assert w["episode_wait_seconds"]==60*sum(counts[:-1])
        assert w["new_episodes"]-w["observed_replies"]==counts[-1]
        for target in s["deadlines"]:
            limit=timedelta(minutes=target["deadline_minutes"])
            states=Counter()
            for a,b,_ in expected:
                if b is None:states[e.STATES[3] if at(cutoff)-a<=limit else e.STATES[2]]+=1
                else:states[e.STATES[0] if b-a<=limit else e.STATES[1]]+=1
            assert target["counts"]==dict((k,states[k]) for k in e.STATES)
        assert sum(b["open_episodes"] for b in r["workload"]["aging_bands"])==counts[-1]


@pytest.mark.parametrize("cutoff,deadlines",[
    ("2024-01-01", [60]),("2024-02-30T00:00:00Z",[60]),("9999-01-01T00:00:00Z",[60]),
    ("2024-01-01T00:00:00Z",[]),("2024-01-01T00:00:00Z",[True]),
    ("2024-01-01T00:00:00Z",[60,60]),("2024-01-01T00:00:00Z",[0]),
    ("2024-01-01T00:00:00Z",[43201]),("2024-01-01T00:00:00Z",list(range(1,10))),
])
def test_bad_settings(cutoff,deadlines):
    with pytest.raises(ValueError):e.settings(cutoff,deadlines)


def test_existing_mining_optional_only_and_no_new_raw_values():
    messages=[cm.Message(at(0),"Please cancel my subscription","PRIVATE_THREAD","","inbound","email"),
              cm.Message(at(60),"Private reply","PRIVATE_THREAD","","outbound","email"),
              cm.Message(at(120),"Please cancel","", "", "inbound","email")]
    assert "response_episodes" not in cm.analyze(messages)
    report=cm.analyze(messages,response_episode_settings=e.settings("2024-01-02T00:00:00Z",[60]))
    a=report["response_episodes"]
    assert a["coverage"]["unthreaded_messages"]==1
    assert a["overall"]["episodes"]==1 and a["overall"]["deadlines"][0]["counts"][e.STATES[0]]==1
    assert "PRIVATE_THREAD" not in json.dumps(a) and "Private reply" not in json.dumps(a)
