"""Independent finite-population calibration oracles and actual retained snapshots."""
from fractions import Fraction
from itertools import product
from datetime import timedelta
import random

import pytest

from server import event_log_prediction_calibration_math as m
from server import event_log_prediction_calibration as c
from server import event_log_prediction_snapshots as snapshots
from server import event_log_workspace as workspace
from test_event_log_prediction_snapshots import _data_dir,_csv,_history,START,MAPPING,SPEC


def row(i,q=10,p=.4,b=.6,state="finished",actual=10,elapsed=10,y=True,activity="A"):
    return {"frozen":{"case_id":str(i),"last_activity":activity,"basis":"activity",
                      "remaining_p10_seconds":q,"median_remaining_seconds":q,"remaining_p90_seconds":q,
                      "outcome_probability":p,"base_rate":b},
            "state":state,"remaining_seconds":actual if state=="finished" else None,
            "elapsed_seconds":elapsed if state=="still_open" else None,"outcome":y if state=="finished" else None}


def test_quantile_ties_and_censored_boundary_are_distinguished():
    rows=[row(0),row(1,actual=9),row(2,actual=11),row(3,state="still_open",elapsed=10),
          row(4,state="still_open",elapsed=11),row(5,state="missing")]
    result=m.quantiles(rows)
    q=result[1]
    assert q["strictly_below"]["known_yes"]==1
    assert q["strictly_below"]["known_no"]==4
    assert q["strictly_below"]["unknown"]==1
    assert q["at_or_below"]["known_yes"]==2
    assert q["at_or_below"]["known_no"]==2
    assert q["at_or_below"]["unknown"]==2
    assert q["diagnosis"]=="not_resolved"
    assert m.quantiles([row(i) for i in range(5)])[1]["diagnosis"]=="target_bracketed_by_empirical_quantile"
    assert m.quantiles([row(i,actual=2) for i in range(5)])[1]["diagnosis"]=="definitely_too_high"
    assert m.quantiles([row(i,state="still_open",elapsed=11) for i in range(5)])[1]["diagnosis"]=="definitely_too_low"
    assert m.quantile_membership(row(1,q=0,state="missing"),"median_remaining_seconds")==("no","unknown")


def test_discrete_calibration_gap_and_bin_boundary():
    rows=[row(i,p=.3,b=1,state="still_open") for i in range(5)]
    r=m.reliability(rows,10,5)
    assert r["model"]["bins"][3]["cases"]==5
    assert r["baseline"]["bins"][9]["cases"]==5
    assert r["model"]["eventual_fixed_bin_ece_lower"]==.1
    assert r["model"]["eventual_fixed_bin_ece_upper"]==.7
    assert r["baseline"]["eventual_fixed_bin_ece_lower"]==0
    assert r["baseline"]["eventual_fixed_bin_ece_upper"]==1


def test_invalid_probability_pairs_excluded_and_small_bins_count_in_ece():
    rows=[row(i,p=.1*i,b=.5,y=False) for i in range(5)]+[row(8,p=None),row(9,b=None)]
    r=m.reliability(rows,10,5)
    assert r["paired_probability_cases"]==5 and r["excluded_invalid_probability_pairs"]==2
    assert r["model"]["supported_bin_cases"]==0
    assert r["model"]["eventual_fixed_bin_ece_lower"]==.2
    assert all(x["mean_predicted"] is None for x in r["model"]["bins"])
    assert r["baseline"]["eventual_fixed_bin_ece_lower"]==.5


def test_independent_exhaustive_binary_label_calibration_oracle():
    rng=random.Random(1562)
    for _ in range(160):
        n=rng.randint(1,8)
        bins=rng.choice([5,10,20])
        rows=[row(i,p=rng.randrange(21)/20,b=rng.randrange(21)/20,
                  state=rng.choice(["finished","still_open","missing","inconsistent"]),y=bool(rng.randrange(2))) for i in range(n)]
        result=m.reliability(rows,bins,1)
        unknown=[i for i,r in enumerate(rows) if r["state"]!="finished"]
        assignments=[]
        for values in product([0,1],repeat=len(unknown)):
            labels=[int(r["outcome"]) if r["state"]=="finished" else None for r in rows]
            for i,y in zip(unknown,values):labels[i]=y
            assignments.append(labels)
        oracle={}
        for arm,key in [("model","outcome_probability"),("baseline","base_rate")]:
            def ece(labels):
                # Oracle bins via direct rational interval tests, not engine indexing.
                total=Fraction(0)
                for k in range(bins):
                    ix=[i for i,r in enumerate(rows)
                        if Fraction(k,bins)<=Fraction(str(r["frozen"][key])) and
                        (Fraction(str(r["frozen"][key]))<Fraction(k+1,bins) or
                         k==bins-1 and r["frozen"][key]==1)]
                    total+=abs(sum(labels[i] for i in ix)-sum((Fraction(str(rows[i]["frozen"][key])) for i in ix),Fraction(0)))
                return total/n
            values=[ece(a) for a in assignments]
            oracle[arm]=values
            assert result[arm]["eventual_fixed_bin_ece_lower"]==pytest.approx(float(min(values)))
            assert result[arm]["eventual_fixed_bin_ece_upper"]==pytest.approx(float(max(values)))
        paired=result["paired_ece_difference"]
        differences=[a-b for a,b in zip(oracle["model"],oracle["baseline"])]
        assert paired["exact_joint_bounds"]
        assert paired["eventual_model_minus_baseline_ece_lower"]==pytest.approx(float(min(differences)))
        assert paired["eventual_model_minus_baseline_ece_upper"]==pytest.approx(float(max(differences)))


def test_independent_quantile_membership_oracle():
    rng=random.Random(6152)
    for _ in range(120):
        rows=[row(i,q=rng.randrange(0,6),actual=rng.randrange(0,8),elapsed=rng.randrange(0,8),
                  state=rng.choice(m.STATES)) for i in range(rng.randint(1,7))]
        bounds=m.quantiles(rows)[0]
        strict_min=strict_max=inc_min=inc_max=0
        for r in rows:
            q=r["frozen"]["remaining_p10_seconds"]
            possible=[r["remaining_seconds"]] if r["state"]=="finished" else list(range(r["elapsed_seconds"] if r["state"]=="still_open" else 0,10))
            strict=[a<q for a in possible];inc=[a<=q for a in possible]
            strict_min+=all(strict);strict_max+=any(strict)
            inc_min+=all(inc);inc_max+=any(inc)
        assert bounds["strictly_below"]["lower"]==strict_min/len(rows)
        assert bounds["strictly_below"]["upper"]==strict_max/len(rows)
        assert bounds["at_or_below"]["lower"]==inc_min/len(rows)
        assert bounds["at_or_below"]["upper"]==inc_max/len(rows)


def test_group_suppression_uses_frozen_support_and_keeps_overall():
    rows=[row(i,activity="Common") for i in range(5)]+[row(5,activity="Private rare label")]
    report=m.build(rows,c.Settings(),False)
    assert report["overall"]["cases"]==6
    assert [g["label"] for g in report["groups"]]==["Common"]
    assert report["cases_in_withheld_or_limited_groups"]==1
    assert m.build(rows,c.Settings(group_by="none"),False)["groups"]==[]


def retained(outcome=True):
    log=workspace.import_log(_csv(_history()),name="orders",source_format="csv",mapping=MAPPING)["log_id"]
    spec={**SPEC}
    if outcome:spec["outcome"]={"kind":"duration_over","seconds":20*3600}
    snap=snapshots.take(log,spec)
    return log,snap["snapshot_id"]


def test_actual_retained_snapshot_and_append_pins():
    log,sid=retained()
    body={"settings":{"bins":5,"minimum_cases":5,"group_by":"activity"}}
    r=c.review(log,sid,body)
    assert r["overall"]["observation_counts"]=={"finished":0,"still_open":6,"missing":0,"inconsistent":0}
    assert r["overall"]["outcome_reliability"]["model"]["eventual_fixed_bin_ece_upper"]==1
    group=r["groups"][0]
    req={**body,"evidence":{"expected_review_sha256":r["review_sha256"],"group_sha256":group["group_sha256"],"arm":"model","bin_index":0,"limit":2}}
    page=c.evidence(log,sid,req)
    assert page["matching_cases"]==6 and page["next_offset"]==2
    assert [x["case_id"] for x in page["cases"]]==["o0","o1"]
    second=c.evidence(log,sid,{**req,"evidence":{**req["evidence"],"offset":2}})
    assert [x["case_id"] for x in second["cases"]]==["o2","o3"]
    later=START+timedelta(days=30,hours=1)
    workspace.append_log(log,_csv([(f"o{i}","Close",(later+timedelta(hours=10)).isoformat()) for i in range(4)]
        +[("o4","Close",(later+timedelta(hours=40)).isoformat()),("x1","Receive",(later+timedelta(hours=50)).isoformat())]),source_format="csv",mapping=MAPPING)
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):c.evidence(log,sid,req)
    after=c.review(log,sid,body)
    assert after["overall"]["observation_counts"]["finished"]==5
    assert after["overall"]["quantiles"][2]["diagnosis"]=="definitely_too_low"
    risk=after["overall"]["outcome_reliability"]["model"]
    assert risk["eventual_fixed_bin_ece_lower"]==pytest.approx(1/6)
    assert risk["eventual_fixed_bin_ece_upper"]==pytest.approx(2/6)
    assert not after["recalibration_applied"]
    assert snapshots.evaluate(log,sid)["finished_cases"]==5
    snapshots.delete(log,sid)
    with pytest.raises(workspace.EventLogWorkspaceError):c.review(log,sid,body)


@pytest.mark.parametrize("settings",[{"bins":True},{"bins":5.0},{"bins":6},{"minimum_cases":4},
    {"minimum_cases":True},{"group_by":"current_activity"},{"extra":1}])
def test_strict_settings(settings):
    with pytest.raises(workspace.EventLogWorkspaceError):c._parse({"settings":settings},False)


def test_invalid_evidence_filters_and_no_outcome():
    log,sid=retained(False)
    body={"settings":{}}
    r=c.review(log,sid,body)
    assert r["overall"]["outcome_reliability"] is None
    for extra in [{"arm":"model"},{"arm":"model","bin_index":10},{"group_sha256":"0"*64},{"arm":"model","bin_index":0}]:
        with pytest.raises(workspace.EventLogWorkspaceError):
            c.evidence(log,sid,{**body,"evidence":{"expected_review_sha256":r["review_sha256"],**extra}})


def test_joint_calibration_same_predictions_always_tie_despite_unknown_labels():
    r=m.reliability([row(i,p=.3,b=.3,state="still_open") for i in range(100)],10,5)
    assert r["model"]["eventual_fixed_bin_ece_upper"]>r["model"]["eventual_fixed_bin_ece_lower"]
    joint=r["paired_ece_difference"]
    assert joint["exact_joint_bounds"] and joint["compressed_assignments_evaluated"]==101
    assert joint["eventual_model_minus_baseline_ece_lower"]==joint["eventual_model_minus_baseline_ece_upper"]==0
    assert joint["direction"]=="always_equal_ece"


def test_joint_calibration_budget_fallback_is_conservative(monkeypatch):
    from server import event_log_joint_calibration as joint
    rows=[row(i,p=i/10,b=(9-i)/10,state="still_open") for i in range(8)]
    exact=m.reliability(rows,10,5)["paired_ece_difference"]
    monkeypatch.setattr(joint,"MAX_ASSIGNMENTS",1)
    bounded=m.reliability(rows,10,5)["paired_ece_difference"]
    assert not bounded["exact_joint_bounds"]
    assert bounded["compressed_assignments_evaluated"]==0
    assert bounded["eventual_model_minus_baseline_ece_lower"]<=exact["eventual_model_minus_baseline_ece_lower"]
    assert bounded["eventual_model_minus_baseline_ece_upper"]>=exact["eventual_model_minus_baseline_ece_upper"]
