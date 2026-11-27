"""Exact clustered stress tests over the actual frozen trial and missing slots."""
from copy import deepcopy
from itertools import combinations, product

import pytest

from shared import agent_procurement_case_robustness as robust
from shared.agent_procurement import digest
from shared.agent_procurement_trials import review
from test_agent_procurement import study
from test_agent_procurement_trials import trial,capture,ledger


def request(trial, captures, policy=None):
    packet,_,_,plan=trial
    value=ledger(plan,captures)
    checked=review(packet,plan,value)
    return packet,{"format":robust.FORMAT,"expected_plan_sha256":checked["plan_sha256"],
        "expected_ledger_sha256":checked["ledger_sha256"],"plan":plan,"captures":value,
        "policy":policy or {},"confirmed":True}


def complete(trial):
    return [capture(trial[1],cid,r) for cid in ("baseline","cheap") for r in (1,2)]


def test_complete_trial_reliability_and_deletion_witnesses(trial):
    packet,req=request(trial,complete(trial))
    copy=deepcopy(req)
    report=robust.operation(packet,req)
    assert req==copy
    assert report==robust.operation(packet,req)
    assert report["distinct_cases"]==3 and report["repeats"]==2
    assert report["baseline_all_repeats_match_case_rate"]==robust._interval(2,2,3)
    candidate=report["comparisons"][0]
    assert candidate["all_repeats_match_case_rate"]==robust._interval(3,3,3)
    assert candidate["full_population"]["intervals"]["match_difference"]==robust._interval(2,2,6)
    one=candidate["case_deletion_sensitivity"][1]
    assert one["remaining_cases"]==2
    assert {k:one["intervals"]["match_difference"][k] for k in ("lower_numerator","upper_numerator","denominator")}==robust._interval(0,2,4)
    assert one["universal_checks"]["match_difference"]["status"]=="pass"
    assert report["robustness_sha256"]==digest({k:v for k,v in report.items() if k!="robustness_sha256"})
    assert not report["execution_authorized"] and not report["automatic_selection_performed"]
    assert not report["confidence_interval_estimated"] and not report["independent_repeats_assumed"]
    assert not report["result_persisted"]


def test_missing_repeats_are_not_clean_or_discarded(trial):
    packet,req=request(trial,[])
    report=robust.operation(packet,req)
    candidate=report["comparisons"][0]
    assert candidate["full_population"]["paired_slots"]==6
    assert candidate["all_repeats_match_case_rate"]==robust._interval(0,3,3)
    assert candidate["full_population"]["intervals"]["match_difference"]==robust._interval(-6,6,6)
    assert candidate["case_deletion_sensitivity"][1]["universal_checks"]["match_difference"]["status"]=="unknown"
    assert candidate["robustness_status"]=="unknown"


def test_repeated_case_failure_and_insufficient_deletion_coverage(trial):
    captures=complete(trial)
    captures[-1]=capture(trial[1],"cheap",2,{"case_1":"transport_failed"})
    packet,req=request(trial,captures,{"maximum_case_deletions":3,"minimum_remaining_cases":2})
    report=robust.operation(packet,req)["comparisons"][0]
    assert report["all_repeats_match_case_rate"]==robust._interval(2,2,3)
    assert report["evaluated_maximum_deletions"]==1 and report["deletion_coverage"]=="unknown"
    assert report["per_repeat"][1]["intervals"]["regression"]["lower_numerator"]>=0
    assert report["robustness_status"]!="pass"


@pytest.mark.parametrize("kind",["plan","ledger","changed_plan","changed_capture","no_consent","extra","bool","invalid_policy"])
def test_exact_pins_and_strict_policy(trial,kind):
    packet,req=request(trial,complete(trial))
    if kind=="plan":req["expected_plan_sha256"]="0"*64
    elif kind=="ledger":req["expected_ledger_sha256"]="0"*64
    elif kind=="changed_plan":req["plan"]["slots"][0]["position"]=999
    elif kind=="changed_capture":req["captures"]["captures"][0]["attempts"][0]["cost_microunits"]=123
    elif kind=="no_consent":req["confirmed"]=False
    elif kind=="extra":req["force_selection"]=True
    elif kind=="bool":req["policy"]["maximum_case_deletions"]=True
    else:req["policy"]["minimum_category_cases"]=0
    with pytest.raises(ValueError):robust.operation(packet,req)


def test_universal_gates_against_explicit_subsets():
    intervals=[(lo,hi) for lo in range(-1,2) for hi in range(lo,2)]
    for values in product(intervals,repeat=3):
        clusters=[{"case_id":str(i),"repeat_count":1,"score_lower":a,"score_upper":b}
                  for i,(a,b) in enumerate(values)]
        for k in range(3):
            sets=list(combinations(range(3),3-k))
            env=robust._deletion_envelope(clusters,"score",k)
            assert env["lower_numerator"]==min(sum(values[i][0] for i in s) for s in sets)
            assert env["upper_numerator"]==max(sum(values[i][1] for i in s) for s in sets)
            for limit,minimum in product((-5000,0,5000),(False,True)):
                def oracle(s):
                    lo=sum(values[i][0] for i in s)*10000
                    hi=sum(values[i][1] for i in s)*10000
                    target=limit*len(s)
                    if minimum:
                        return "pass" if lo>=target else "fail" if hi<target else "unknown"
                    return "pass" if hi<=target else "fail" if lo>target else "unknown"
                statuses=[oracle(s) for s in sets]
                expected="fail" if "fail" in statuses else "pass" if all(s=="pass" for s in statuses) else "unknown"
                got=robust._universal_check(clusters,"score",k,env,limit,minimum)
                assert got["status"]==expected
                witness=got["definite_failure_witness_deleted_case_ids"]
                if expected=="fail":
                    assert len(witness)==k
                    assert oracle(tuple(i for i in range(3) if str(i) not in witness))=="fail"
                else:
                    assert witness is None


def test_cli_exclusive_output(trial,tmp_path):
    import json
    packet,req=request(trial,complete(trial))
    p,d,o=[tmp_path/x for x in ("packet.json","request.json","report.json")]
    p.write_text(json.dumps(packet));d.write_text(json.dumps(req))
    assert robust.main([str(p),str(d),str(o)])==0
    assert json.loads(o.read_text())==robust.operation(packet,req)
    with pytest.raises(SystemExit):robust.main([str(p),str(d),str(o)])
