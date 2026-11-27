"""Independent case-mix identities, retained event scope and stale review checks."""
from datetime import datetime,timedelta
from fractions import Fraction
from collections import defaultdict
import random

import pytest

from server import event_log_case_mix_math as m
from server import event_log_case_mix as review
from server import event_log_impact as impact
from server import event_log_workspace as workspace
from test_event_log_impact import _data_dir,_log,AT,HOUR,_after

DATE=datetime(2024,3,1)


def synthetic(cells,minimum=5):
    groups=defaultdict(list);labels={}
    for (group,period,label),values in cells.items():
        started=DATE+timedelta(days=-3 if period=="baseline" else 3)
        for v in values:
            key=f"case{len(labels):05d}"
            labels[key]=label if isinstance(label,tuple) else ("observed",label)
            groups[group].append((key,started,started+timedelta(seconds=1),v))
    return m.calculate(groups,labels,m.Settings(dimension="first_activity",minimum_cases=minimum),
        DATE,None,None,None,False,impact._case_period,{"synthetic":True},len(labels))[0]


def test_apparent_improvement_is_entirely_composition_on_common_support():
    r=synthetic({("all","baseline","A"):[10]*5,("all","baseline","B"):[30]*15,
                 ("all","follow_up","A"):[10]*15,("all","follow_up","B"):[30]*5,
                 ("all","follow_up","C"):[50]*5})
    g=r["groups"]["all"]
    assert g["raw_change"]==-3
    assert g["common_support_change"]==-10
    assert g["symmetric_within_stratum_component"]==0
    assert g["symmetric_composition_component"]==-10
    assert g["raw_minus_common_support_residual"]==7
    assert g["standardized_change"]==0
    assert g["periods"]["follow_up"]["common_support_cases"]==20
    assert g["periods"]["follow_up"]["common_support_share"]==.8
    assert r["common_support_strata"]==2
    assert next(s for s in r["strata"] if s["label"]=="C")["common_support"] is False


def test_common_support_intersects_every_treated_control_cell():
    cells={(g,p,s):[v]*5 for g,v in [("treated",10),("control",20)]
           for p in ("baseline","follow_up") for s in ("A","B")}
    cells[("treated","follow_up","A")]=[4]*5
    cells[("control","follow_up","B")]=[20]*4
    cells[("treated","baseline",("missing",None))]=[100]*5
    r=synthetic(cells)
    assert r["common_support_strata"]==1
    assert r["groups"]["treated"]["standardized_change"]==-6
    assert r["groups"]["control"]["standardized_change"]==0
    assert r["control_adjusted"]["standardized_difference_in_differences"]==-6
    b=next(s for s in r["strata"] if s["label"]=="B")
    assert b["cells"]["control"]["follow_up"]["mean"] is None
    assert b["exclusion_reason"]=="insufficient_support_in_one_or_more_cells"
    missing=next(s for s in r["strata"] if s["label_kind"]=="missing")
    assert missing["exclusion_reason"]=="missing_or_ambiguous_start_label"


def test_empty_common_support_no_invented_means():
    r=synthetic({("all","baseline","A"):[1]*5,("all","follow_up","B"):[4]*5})
    g=r["groups"]["all"]
    assert g["raw_change"]==3
    assert g["standardized_change"] is None and g["common_support_change"] is None
    assert g["raw_minus_common_support_residual"] is None
    assert not r["common_support_available"]


def test_randomized_independent_standardization_and_decomposition_oracle():
    rng=random.Random(1561)
    for _ in range(160):
        names=["treated","control"] if rng.randrange(2) else ["all"]
        keys=[(g,p) for g in names for p in ("baseline","follow_up")]
        labels=["A","B","C","D"]
        cells={(g,p,s):[rng.randrange(0,100)/10 for _ in range(rng.randint(0,9))]
               for g,p in keys for s in labels}
        # Ensure all group/period rows exist for the synthetic helper.
        for g,p in keys:cells[g,p,"A"]=[rng.randrange(100)/10 for _ in range(5)]
        r=synthetic(cells)
        support=[s for s in labels if all(len(cells[g,p,s])>=5 for g,p in keys)]
        assert r["common_support_strata"]==len(support)
        def avg(v):return sum((Fraction(str(x)) for x in v),Fraction(0))/len(v)
        def population(g,p,strata):return [v for s in strata for v in cells[g,p,s]]
        weights={s:sum((Fraction(len(cells[g,p,s]),len(population(g,p,support))) for g,p in keys),Fraction(0))/len(keys) for s in support}
        assert sum(weights.values())==1
        for g in names:
            before,after=(population(g,p,labels) for p in ("baseline","follow_up"))
            cb,ca=(population(g,p,support) for p in ("baseline","follow_up"))
            change=avg(after)-avg(before)
            common=avg(ca)-avg(cb)
            result=r["groups"][g]
            assert result["raw_change"]==pytest.approx(float(change))
            assert result["common_support_change"]==pytest.approx(float(common))
            # Independent path-average decomposition: change means at
            # baseline weights, then at follow-up weights; average the paths.
            path0=sum(Fraction(len(cells[g,"baseline",s]),len(cb))*(avg(cells[g,"follow_up",s])-avg(cells[g,"baseline",s])) for s in support)
            path1=sum(Fraction(len(cells[g,"follow_up",s]),len(ca))*(avg(cells[g,"follow_up",s])-avg(cells[g,"baseline",s])) for s in support)
            within=(path0+path1)/2
            assert result["symmetric_within_stratum_component"]==pytest.approx(float(within))
            assert result["symmetric_composition_component"]==pytest.approx(float(common-within))
            assert result["raw_minus_common_support_residual"]==pytest.approx(float(change-common))
            standardized=sum(weights[s]*(avg(cells[g,"follow_up",s])-avg(cells[g,"baseline",s])) for s in support)
            assert result["standardized_change"]==pytest.approx(float(standardized))
            assert result["decomposition_exact_before_display_rounding"]


def body(**settings):
    return {"analysis":{"intervention_at":AT,"rounds":100},
            "case_mix":{"dimension":"first_resource","minimum_cases":10,**settings}}


def test_real_retained_log_population_and_pinned_paging():
    log=_log(480,lambda i,t,team:48-(24 if team=="A" and _after(t) else 0))
    spec=body()
    r=review.review(log,spec)
    assert r["population"]["projected_cases"]==480
    assert r["population"]["included_period_cases"]==480
    assert r["common_support_strata"]==2
    assert r["groups"]["all"]["standardized_change"]==-12*HOUR
    a=next(s for s in r["strata"] if s["label"]=="A")
    request={**spec,"expected_review_sha256":r["review_sha256"],"stratum_sha256":a["stratum_sha256"],
             "group":"all","period":"follow_up","limit":17}
    keys=[]
    while True:
        page=review.evidence(log,request)
        assert page["selected_cases"]==120
        assert all(c["value"]==24*HOUR for c in page["cases"])
        keys.extend(c["case_id"] for c in page["cases"])
        if page["next_after"] is None:break
        request["after"]=page["next_after"]
    assert len(keys)==len(set(keys))==120
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):
        review.evidence(log,{**request,"expected_review_sha256":"0"*64})
    with workspace._LOCK:
        conn=workspace._connect()
        try:conn.execute("UPDATE events SET resource='changed' WHERE log_id=? AND activity='Open'",[log])
        finally:conn.close()
    with pytest.raises(workspace.EventLogWorkspaceError,match="changed"):review.evidence(log,request)


def test_tied_and_missing_start_resources_are_not_silently_assigned():
    log=_log(480,lambda i,t,team:48)
    with workspace._LOCK:
        conn=workspace._connect()
        try:
            conn.execute("UPDATE events SET resource='' WHERE log_id=? AND activity='Open' AND resource='A'",[log])
            conn.execute("UPDATE events SET ts=ts-INTERVAL '48 hours', resource='Other' WHERE log_id=? AND activity='Close' AND resource='B'",[log])
        finally:conn.close()
    r=review.review(log,body())
    assert r["common_support_strata"]==0
    assert {s["label_kind"] for s in r["strata"]}=={"missing","ambiguous"}
    assert r["groups"]["all"]["standardized_change"] is None


def test_real_control_and_period_exclusions_match_original_impact():
    log=_log(480,lambda i,t,team:48-(24 if team=="A" and _after(t) else 0))
    spec=body(dimension="utc_start_weekday",minimum_cases=5)
    spec["analysis"].update(treated={"resource":"A"},control={"resource":"B"},window_days=28,exclude_spanning=True)
    r=review.review(log,spec)
    original=impact.analyze(log,spec["analysis"])
    for g in ("treated","control"):
        for period in ("baseline","follow_up"):
            assert r["groups"][g]["periods"][period]["included_cases"]==original["groups"][g][period]["cases"]
    assert r["control_adjusted"]["standardized_difference_in_differences"]==pytest.approx(-24*HOUR)
    assert not r["causal_effect_verified"]


@pytest.mark.parametrize("changes",[{"dimension":"end_activity"},{"minimum_cases":True},{"minimum_cases":4},{"extra":1}])
def test_invalid_case_mix_settings(changes):
    log=_log(480,lambda i,t,team:48)
    with pytest.raises(workspace.EventLogWorkspaceError):review.review(log,body(**changes))


def test_projected_case_bound_refuses_instead_of_sampling(monkeypatch):
    log=_log(480,lambda i,t,team:48)
    monkeypatch.setattr(m,"MAX_CASES",10)
    with pytest.raises(workspace.EventLogWorkspaceError,match="bounded projected"):review.review(log,body())


def test_reference_and_threshold_sensitivity_is_exact_and_no_imputation():
    r=synthetic({("all","baseline","A"):[10]*5,("all","baseline","B"):[30]*15,
                 ("all","follow_up","A"):[20]*15,("all","follow_up","B"):[30]*5})
    primary=r["sensitivity"]["thresholds"][0]
    by_basis={x["reference_basis"]:x["groups"]["all"]["standardized_change"] for x in primary["reference_comparisons"]}
    assert by_basis=={"equal_cell":5,"baseline_cell":2.5,"follow_up_cell":7.5,"pooled_case":5,"equal_stratum":5}
    assert primary["group_change_envelopes"]["all"]=={"minimum_change":2.5,"maximum_change":7.5,"strict_direction_agrees":True,"all_zero":False}
    assert r["sensitivity"]["additional_case_scans"]==0


def test_real_sensitivity_thresholds_can_remove_all_support():
    log=_log(480,lambda i,t,team:48)
    spec=body(sensitivity_thresholds=[5,120,121])
    r=review.review(log,spec)
    levels=r["sensitivity"]["thresholds"]
    assert [x["minimum_cases"] for x in levels]==[5,10,120,121]
    assert [x["common_support_strata"] for x in levels]==[2,2,2,0]
    assert levels[1]["primary_threshold"]
    assert levels[0]["group_change_envelopes"]["all"]["all_zero"]
    assert all(x["groups"]["all"]["standardized_change"] is None for x in levels[-1]["reference_comparisons"])


@pytest.mark.parametrize("thresholds",[[True],[4],[10001],[10,10],list(range(5,13))])
def test_bad_sensitivity_thresholds(thresholds):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        m.Settings(dimension="first_activity",sensitivity_thresholds=thresholds)
