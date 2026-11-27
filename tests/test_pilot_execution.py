"""Counterbalanced schedules and exact attempt history for a prospective cohort."""
from copy import deepcopy
from collections import Counter
from itertools import permutations

import pytest

from evals import prospective_pilot_cohort as cohort
from evals import pilot_execution as execution


def plan(n=3,groups=1):
    return cohort.freeze([{"episode_id":f"e{i}","team_id":f"team{i%groups}",
                          "model_id":"model","source_state_id":f"s{i}",
                          "truth_sha256":cohort.digest({"truth":i})}
                          for i in range(n)],confirmed=True)


def setup(p=None,review_protocol="single"):
    p=p or plan()
    kw={"expected_plan_sha256":p["plan_sha256"],"confirmed":True}
    s=execution.schedule(p,seed="a"*64,review_protocol=review_protocol,**kw)
    kw["expected_schedule_sha256"]=s["schedule_sha256"]
    ledger=execution.initialize(p,s,**kw)
    return p,s,ledger


def kwargs(p,s,l):
    return {"expected_plan_sha256":p["plan_sha256"],"expected_schedule_sha256":s["schedule_sha256"],
            "expected_ledger_sha256":l["ledger_sha256"],"confirmed":True}


def append(p,s,l,actions):
    return execution.append(p,s,l,actions,**kwargs(p,s,l))


def start(p,eid,arm,now,session=None):
    row=next(r for r in p["episodes"] if r["episode_id"]==eid)
    return {"episode_id":eid,"arm":arm,"action":"start","declared_at_ms":now,
            "session_id":session or f"{eid}:{arm}","model_id":row["model_id"],
            "source_state_id":row["source_state_id"]}


def finish(eid,arm,now,material=None,transport=None):
    return {"episode_id":eid,"arm":arm,"action":"completed","declared_at_ms":now,
            "material_sha256":material or cohort.digest(["material",eid,arm]),
            "transport_sha256":transport or cohort.digest(["transport",eid,arm])}


def disposition(eid,arm,now,status):
    return {"episode_id":eid,"arm":arm,"declared_at_ms":now,"action":status}


def test_balanced_position_oracle_across_seeds_and_strata():
    for n in range(1,37):
        for groups in (1,2,5):
            p=plan(n,groups)
            for seed in ("0"*64,"a"*64,"f"*64):
                s=execution.schedule(p,expected_plan_sha256=p["plan_sha256"],seed=seed,confirmed=True)
                assert {r["episode_id"] for r in s["episodes"]}=={r["episode_id"] for r in p["episodes"]}
                assert len(s["episodes"])==n
                for summary in s["strata"]:
                    rows=[r for r in s["episodes"] if (r["team_id"],r["model_id"])==
                          (summary["team_id"],summary["model_id"])]
                    assert len(rows)==summary["episodes"]
                    for arm in cohort.ARMS:
                        counts=[sum(r["arm_order"][i]==arm for r in rows) for i in range(3)]
                        assert counts==summary["arm_position_counts"][arm]
                        assert max(counts)-min(counts)<=1
                    for block in {r["block"] for r in rows}:
                        orders=[tuple(r["arm_order"]) for r in rows if r["block"]==block]
                        assert len(orders)==len(set(orders))
                        if len(orders)==6:
                            assert set(orders)==set(permutations(cohort.ARMS))
                assert s==execution.schedule(p,expected_plan_sha256=p["plan_sha256"],seed=seed,confirmed=True)


def test_progress_order_and_terminal_attrition():
    p,s,l=setup();before=deepcopy(l)
    eid=s["episodes"][0]["episode_id"];a,b,c=s["episodes"][0]["arm_order"]
    actions=[start(p,eid,a,1),finish(eid,a,2),start(p,eid,b,3),
             disposition(eid,b,4,"model_failed"),disposition(eid,c,5,"withdrawn")]
    next=append(p,s,l,actions)
    assert l==before and next["revision"]==5
    assert next["events"][0]["previous_sha256"]==l["history_sha256"]
    assert next["events"][-1]["event_sha256"]==next["history_sha256"]
    progress=execution.progress(p,s,next,**kwargs(p,s,next))
    by_arm={r["arm"]:r for r in progress["cells"] if r["episode_id"]==eid}
    assert [by_arm[x]["state"] for x in (a,b,c)]==["completed","model_failed","withdrawn"]
    assert not progress["all_attempts_completed"] and not progress["session_freshness_verified"]
    assert not any(row["next_action_available"] for row in by_arm.values())
    assert sum(r["next_action_available"] for r in progress["cells"])==2


@pytest.mark.parametrize("kind",["wrong_order","duplicate_start","no_start","retry","session_reuse",
    "receipt_reuse","material_reuse","wrong_model","wrong_source","clock","bool_clock","unknown_cell",
    "unplanned_action","extra","failure_before_start","stale_pin","changed_schedule"])
def test_fail_closed_and_atomic_append(kind):
    p,s,l=setup();eid=s["episodes"][0]["episode_id"];a,b,c=s["episodes"][0]["arm_order"]
    first=start(p,eid,a,1);second=finish(eid,a,2);third=start(p,eid,b,3);fourth=finish(eid,b,4)
    actions=[first,second,third,fourth];kw=kwargs(p,s,l)
    if kind=="wrong_order":actions=[third]
    elif kind=="duplicate_start":actions=[first,first]
    elif kind=="no_start":actions=[second]
    elif kind=="retry":actions=[first,second,first]
    elif kind=="session_reuse":third["session_id"]=first["session_id"]
    elif kind=="receipt_reuse":fourth["transport_sha256"]=second["transport_sha256"]
    elif kind=="material_reuse":fourth["material_sha256"]=second["material_sha256"]
    elif kind=="wrong_model":first["model_id"]="another"
    elif kind=="wrong_source":first["source_state_id"]="another"
    elif kind=="clock":third["declared_at_ms"]=0
    elif kind=="bool_clock":first["declared_at_ms"]=True
    elif kind=="unknown_cell":first["episode_id"]="unplanned"
    elif kind=="unplanned_action":first["action"]="skip"
    elif kind=="extra":first["raw_prompt"]="private"
    elif kind=="failure_before_start":actions=[disposition(eid,a,0,"model_failed")]
    elif kind=="stale_pin":kw["expected_ledger_sha256"]="0"*64
    else:s["episodes"][0]["arm_order"].reverse()
    original=deepcopy(l)
    with pytest.raises(ValueError):execution.append(p,s,l,actions,**kw)
    assert l==original


def test_rehashed_illegal_history_rejected():
    p,s,l=setup();eid=s["episodes"][0]["episode_id"];a,b,c=s["episodes"][0]["arm_order"]
    bad=disposition(eid,b,1,"withdrawn")
    body={"revision":1,"previous_sha256":l["history_sha256"],"action":bad}
    h=cohort.digest(body)
    forged=execution._header(p["plan_sha256"],s["schedule_sha256"],[{**body,"event_sha256":h}],h)
    with pytest.raises(ValueError):execution.progress(p,s,forged,**kwargs(p,s,forged))


def test_all_arms_terminal_but_failures_never_completed():
    p,s,l=setup(plan(2))
    actions=[]
    for row in s["episodes"]:
        for arm in row["arm_order"]:
            actions.append(disposition(row["episode_id"],arm,len(actions),"source_unavailable"))
    result=append(p,s,l,actions)
    report=execution.progress(p,s,result,**kwargs(p,s,result))
    assert report["all_attempts_terminal"] and not report["all_attempts_completed"]
    assert report["arm_counts"]=={a:{"source_unavailable":2} for a in cohort.ARMS}


def test_cli_exclusive_snapshots(tmp_path):
    p,s,l=setup()
    plan_path=tmp_path/"plan.json";cohort._write(plan_path,p)
    schedule_path=tmp_path/"schedule.json";ledger_path=tmp_path/"ledger.json"
    common=["--plan",str(plan_path),"--plan-sha256",p["plan_sha256"],"--confirm"]
    assert execution.main(["schedule",*common,"--seed","a"*64,"--review-protocol","single","--out",str(schedule_path)])==0
    assert cohort._load(schedule_path)==s
    common+=["--schedule",str(schedule_path),"--schedule-sha256",s["schedule_sha256"]]
    assert execution.main(["initialize",*common,"--out",str(ledger_path)])==0
    assert cohort._load(ledger_path)==l
    common+=["--ledger",str(ledger_path),"--ledger-sha256",l["ledger_sha256"]]
    actions=tmp_path/"actions.json";out=tmp_path/"next.json"
    row=s["episodes"][0]
    cohort._write(actions,[start(p,row["episode_id"],row["arm_order"][0],1)])
    args=["append",*common,"--actions",str(actions),"--out",str(out)]
    assert execution.main(args)==0
    assert cohort._load(out)["revision"]==1
    with pytest.raises(SystemExit):execution.main(args)
    assert cohort._load(ledger_path)==l


def test_withdraw_remaining_keeps_completed_history_and_no_material_coverage():
    p,s,l=setup(plan(1));eid=s["episodes"][0]["episode_id"];a,b,c=s["episodes"][0]["arm_order"]
    l=append(p,s,l,[start(p,eid,a,1),finish(eid,a,2),start(p,eid,b,3)])
    next=execution.withdraw_remaining(p,s,l,episode_id=eid,declared_at_ms=4,**kwargs(p,s,l))
    assert next["events"][:l["revision"]]==l["events"]
    report=execution.progress(p,s,next,**kwargs(p,s,next))
    assert report["all_attempts_terminal"] and not report["all_attempts_completed"]
    assert report["arm_counts"]=={a:{"completed":1},b:{"withdrawn":1},c:{"withdrawn":1}}
    pos=report["position_diagnostics"][0]["positions"]
    assert pos[a][0]=={"position":1,"planned":1,"started":1,"completed":1,"terminal_noncompletion":0}
    assert pos[b][1]=={"position":2,"planned":1,"started":1,"completed":0,"terminal_noncompletion":1}
    assert pos[c][2]=={"position":3,"planned":1,"started":0,"completed":0,"terminal_noncompletion":1}
    coverage=execution.coverage_without_material(p,s,next,**kwargs(p,s,next))
    assert coverage["coverage"]["arm_dispositions"]=={a:{"review_unavailable":1},b:{"withdrawn":1},c:{"withdrawn":1}}
    assert not coverage["review_material_used"] and not coverage["canonical_scorer_invoked"]
    assert all(coverage["coverage"]["bounds"][arm]["continuation_success"]["unknown"]==1 for arm in cohort.ARMS)
    with pytest.raises(ValueError):execution.withdraw_remaining(p,s,next,episode_id=eid,declared_at_ms=5,**kwargs(p,s,next))
    with pytest.raises(ValueError):append(p,s,next,[start(p,eid,b,5)])


def test_preregistered_protocol_and_canonical_numeric_metadata():
    p,s,l=setup(review_protocol="dual_adjudication")
    assert s["review_protocol"]=="dual_adjudication"
    with pytest.raises(ValueError):
        execution.schedule(p,expected_plan_sha256=p["plan_sha256"],seed="a"*64,
                           confirmed=True,review_protocol="choose_after_review")
    changed=deepcopy(s)
    changed["episodes"][0]["block"]=True
    assert changed==s  # Python equality is weaker than canonical JSON equality.
    with pytest.raises(ValueError):
        execution.initialize(p,changed,expected_plan_sha256=p["plan_sha256"],
                             expected_schedule_sha256=s["schedule_sha256"],confirmed=True)
    changed=deepcopy(s);changed["review_protocol"]="single"
    with pytest.raises(ValueError):
        execution.initialize(p,changed,expected_plan_sha256=p["plan_sha256"],
                             expected_schedule_sha256=s["schedule_sha256"],confirmed=True)
