"""Prospective coverage, incomplete outcomes, strict pins and denominator invariants."""
from copy import deepcopy
from itertools import product
import json

import pytest

from evals import prospective_pilot_cohort as cohort


def roster(n=3):
    return [{"episode_id":f"e{i}","team_id":f"team{i%2}","model_id":"model",
             "source_state_id":f"s{i}","truth_sha256":cohort.digest({"truth":i})}
            for i in range(n)]


def observed(row,arm,success=True,harm=False):
    return {k:row[k] for k in ("episode_id","model_id","source_state_id","truth_sha256")} | {
        "arm":arm,"status":"reviewed","review_sha256":cohort.digest([row["episode_id"],arm]),
        "continuation_success":success,"consequential_wrong_action":harm}


def review(plan,rows):
    return cohort.inspect(plan,rows,expected_plan_sha256=plan["plan_sha256"],confirmed=True)


def test_frozen_denominator_and_missing_worklist():
    rows=roster();plan=cohort.freeze(rows,confirmed=True)
    records=[observed(rows[0],a) for a in cohort.ARMS]
    records += [{"episode_id":"e1","arm":"goal","status":"withdrawn"},
                {"episode_id":"e2","arm":"control","status":"model_failed"}]
    result=review(plan,records)
    assert result["enrolled"]==3 and result["complete_paired_episodes"]==1
    assert len(result["incomplete_worklist"])==6
    assert result["arm_dispositions"]["goal"]=={"not_reported":1,"reviewed":1,"withdrawn":1}
    assert result["bounds"]["goal"]["continuation_success"]=={
        "observed_positive":1,"reviewed":1,"unknown":2,"denominator":3,
        "lower_numerator":1,"upper_numerator":3}
    assert result["team_coverage"]["team0"]=={"enrolled":2,"all_arms_reviewed":1}
    assert not result["canonical_score_eligible"] and not result["product_effectiveness_verified"]
    assert result==review(plan,list(reversed(records)))


def test_all_arms_complete_but_not_effectiveness_claim():
    rows=roster();plan=cohort.freeze(rows,confirmed=True)
    result=review(plan,[observed(row,a,a=="goal",False) for row in rows for a in cohort.ARMS])
    assert result["canonical_score_eligible"] and result["complete_coverage"]
    assert result["contrasts"]["goal_vs_control"]["continuation_success"]=={
        "lower_numerator":3,"upper_numerator":3,"denominator":3,"paired_reviewed":3}
    assert not result["provenance_verified"] and not result["product_effectiveness_verified"]


def test_exact_bounds_against_exhaustive_completions():
    # 3^6 assignments of observed success/failure/unknown for three episodes
    # and two arms. Compare against all possible completions, not another formula.
    rows=roster();plan=cohort.freeze(rows,confirmed=True)
    for partial in product((False,True,None),repeat=6):
        records=[observed(rows[i%3],("control","goal")[i//3],v,not v)
                 for i,v in enumerate(partial) if v is not None]
        result=review(plan,records)
        possibilities=[]
        for fill in product((False,True),repeat=partial.count(None)):
            values=iter(fill)
            completed=[next(values) if v is None else v for v in partial]
            possibilities.append(sum(completed[3:])-sum(completed[:3]))
        delta=result["contrasts"]["goal_vs_control"]["continuation_success"]
        assert (delta["lower_numerator"],delta["upper_numerator"])==(min(possibilities),max(possibilities))
        for arm,offset in (("control",0),("goal",3)):
            observed_flags=partial[offset:offset+3]
            lower=sum(v is True for v in observed_flags)
            upper=lower+observed_flags.count(None)
            bounds=result["bounds"][arm]["continuation_success"]
            assert (bounds["lower_numerator"],bounds["upper_numerator"])==(lower,upper)


@pytest.mark.parametrize("kind",["plan_pin","plan_tamper","extra","duplicate","unplanned","unknown_arm",
    "wrong_model","wrong_source","wrong_truth","bad_review_pin","nonboolean","missing_with_outcome",
    "unknown_status","no_confirmation"])
def test_invalid_and_stale_records_refused(kind):
    rows=roster();plan=cohort.freeze(rows,confirmed=True);pin=plan["plan_sha256"]
    records=[observed(rows[0],"control")];confirmed=True
    if kind=="plan_pin":pin="0"*64
    elif kind=="plan_tamper":plan["episodes"].pop()
    elif kind=="extra":records[0]["raw_prompt"]="private"
    elif kind=="duplicate":records.append(deepcopy(records[0]))
    elif kind=="unplanned":records[0]["episode_id"]="e99"
    elif kind=="unknown_arm":records[0]["arm"]="new"
    elif kind=="wrong_model":records[0]["model_id"]="another"
    elif kind=="wrong_source":records[0]["source_state_id"]="another"
    elif kind=="wrong_truth":records[0]["truth_sha256"]="0"*64
    elif kind=="bad_review_pin":records[0]["review_sha256"]="bad"
    elif kind=="nonboolean":records[0]["continuation_success"]=1
    elif kind=="missing_with_outcome":records[0]["status"]="withdrawn"
    elif kind=="unknown_status":records[0]["status"]="claimed"
    else:confirmed=False
    with pytest.raises(ValueError):
        cohort.inspect(plan,records,expected_plan_sha256=pin,confirmed=confirmed)


def test_roster_strictness_and_cli_private_exclusive_outputs(tmp_path):
    rows=roster();p=cohort.freeze(rows,confirmed=True)
    assert p==cohort.freeze(rows[::-1],confirmed=True)
    for invalid in ([],rows+rows[:1],[{**rows[0],"name":"employee"}],
                    [{**rows[0],"episode_id":"contains spaces"}]):
        with pytest.raises(ValueError):cohort.freeze(invalid,confirmed=True)
    with pytest.raises(ValueError):cohort.freeze(rows,confirmed="true")
    inp,plan,obs,out=[tmp_path/p for p in ("roster.json","plan.json","obs.json","out.json")]
    inp.write_text(json.dumps(rows));obs.write_text("[]")
    args=["freeze","--roster",str(inp),"--out",str(plan),"--confirm"]
    assert cohort.main(args)==0
    assert cohort._load(plan)==p
    assert cohort.main(["inspect","--plan",str(plan),"--plan-sha256",p["plan_sha256"],
                        "--observations",str(obs),"--out",str(out),"--confirm"])==0
    assert cohort._load(out)["complete_paired_episodes"]==0
    with pytest.raises(SystemExit):cohort.main(args)
    assert cohort._load(plan)==p
    inp.write_text('{"a":1,"a":2}')
    with pytest.raises(ValueError):cohort._load(inp)
    inp.write_text('{"a":NaN}')
    with pytest.raises(ValueError):cohort._load(inp)


def paired(n=3):
    truth=[{"episode_id":f"e{i}","correct_resources":["resource"],
            "correct_agent_runs":[],"real_observed_work":False} for i in range(n)]
    plan=cohort.freeze([{**row,"truth_sha256":cohort.digest(t)}
                        for row,t in zip(roster(n),truth)],confirmed=True)
    runs={}
    for arm in cohort.ARMS:
        runs[arm]=[{"episode_id":row["episode_id"],"model_id":row["model_id"],
                    "source_state_id":row["source_state_id"],"recorded_model_completion":True,
                    "independent_review":{"reviewer_id":"reviewer","selected_resources":["resource"],
                        "selected_agent_runs":[],"clarification_questions":0,"wrong_assumptions":0,
                        "continuation_success":arm=="goal","consequential_wrong_action":False}}
                   for row in plan["episodes"]]
    return plan,truth,runs


def score(plan,truth,runs,dispositions=()):
    return cohort.score(plan,truth,runs,list(dispositions),
                        expected_plan_sha256=plan["plan_sha256"],confirmed=True)


def test_scoring_reuses_existing_canonical_report_and_pins_runs():
    from evals.product_value import report
    plan,truth,runs=paired()
    result=score(plan,truth,runs)
    assert result["canonical_report"]==report(truth,*(runs[a] for a in cohort.ARMS))
    assert result["canonical_scorer_invoked"] and not result["decision_withheld_for_incomplete_coverage"]
    assert result["run_sha256"]["goal"]==cohort.digest(runs["goal"])
    assert result["coverage"]["complete_paired_episodes"]==3
    assert result["canonical_report"]["status"]=="pilot_only"
    assert result["score_sha256"]==cohort.digest({k:v for k,v in result.items() if k!="score_sha256"})


def test_incomplete_pilot_never_calls_canonical_scorer(monkeypatch):
    from evals import product_value
    plan,truth,runs=paired()
    runs["goal"].pop()
    def forbidden(*args):
        pytest.fail("partial cohort must not invoke canonical scorer")
    monkeypatch.setattr(product_value,"report",forbidden)
    result=score(plan,truth,runs,[{"episode_id":"e2","arm":"goal","status":"withdrawn"}])
    assert result["canonical_report"] is None
    assert result["decision_withheld_for_incomplete_coverage"]
    assert result["coverage"]["enrolled"]==3 and result["coverage"]["complete_paired_episodes"]==2


@pytest.mark.parametrize("kind",["truth_subset","truth_mutation","run_model","run_source",
                                 "not_completed","no_review","duplicate_disposition","unplanned_run"])
def test_scoring_rejects_mismatched_review_material(kind):
    plan,truth,runs=paired();dispositions=[]
    if kind=="truth_subset":truth.pop()
    elif kind=="truth_mutation":truth[0]["correct_resources"]=[]
    elif kind=="run_model":runs["goal"][0]["model_id"]="another"
    elif kind=="run_source":runs["goal"][0]["source_state_id"]="another"
    elif kind=="not_completed":runs["goal"][0]["recorded_model_completion"]=False
    elif kind=="no_review":runs["goal"][0].pop("independent_review")
    elif kind=="duplicate_disposition":dispositions=[{"episode_id":"e0","arm":"goal","status":"withdrawn"}]
    else:runs["goal"][0]["episode_id"]="e99"
    with pytest.raises(ValueError):score(plan,truth,runs,dispositions)


def test_score_cli_jsonl(tmp_path):
    plan,truth,runs=paired()
    p=tmp_path/"plan.json";p.write_text(json.dumps(plan))
    d=tmp_path/"missing.json";d.write_text("[]")
    e=tmp_path/"episodes.jsonl";e.write_text("\n".join(map(json.dumps,truth)))
    o=tmp_path/"score.json"
    args=["score","--plan",str(p),"--plan-sha256",plan["plan_sha256"],"--episodes",str(e),
          "--dispositions",str(d),"--out",str(o),"--confirm"]
    for arm,rows in runs.items():
        f=tmp_path/(arm+".jsonl");f.write_text("\n".join(map(json.dumps,rows)))
        args.extend(["--"+arm,str(f)])
    assert cohort.main(args)==0
    assert cohort._load(o)==score(plan,truth,runs)
    with pytest.raises(SystemExit):cohort.main(args)
