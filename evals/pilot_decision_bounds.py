"""Prospective decision sensitivity across all enrolled cases and teams.

No sampling inference, p-values, verified causal effects, or automatic rollout.
Unknown outcomes remain unknown. All comparisons use exact rational bounds.
"""
from collections import defaultdict
from fractions import Fraction

ARMS=("control","raw","goal")
OUTCOMES=("continuation_success","consequential_wrong_action")
COMPARISONS=(("control","raw"),("control","goal"),("raw","goal"))
POLICY_FIELDS={
    "min_enrolled":(1,500),"min_teams":(2,500),"min_episodes_per_team":(1,500),
    "min_goal_success_bps":(0,10000),"max_goal_wrong_action_bps":(0,10000),
    "min_goal_success_delta_bps":(0,10000),"max_goal_wrong_action_delta_bps":(-10000,10000),
    "min_leave_one_team_success_delta_bps":(-10000,10000),
    "max_leave_one_team_wrong_action_delta_bps":(-10000,10000),
}


def policy(value):
    if not isinstance(value,dict) or set(value)!=set(POLICY_FIELDS):
        raise ValueError("exact explicit decision policy required")
    if any(type(value[k]) is not int or not lo<=value[k]<=hi
           for k,(lo,hi) in POLICY_FIELDS.items()):
        raise ValueError("invalid decision threshold")
    return dict(value)


def ratio(f):
    return {"numerator":f.numerator,"denominator":f.denominator}


def bounds(lo,hi):
    return {"lower":ratio(lo),"upper":ratio(hi)}


def binary(row,field):
    if row is None or row.get("status")!="reviewed":
        return 0,1
    v=row[field]
    if type(v) is not bool:raise ValueError("reviewed Boolean required")
    return int(v),int(v)


def interval(ids,cells,left,right,field):
    lo=hi=0
    for eid in ids:
        a,b=binary(cells.get((eid,left)),field) if left else (0,0)
        c,d=binary(cells.get((eid,right)),field)
        lo+=c-b;hi+=d-a
    return lo,hi


def decision_check(name,lo,hi,threshold,direction):
    limit=Fraction(threshold,10000)
    if direction=="min":
        status="pass" if lo>=limit else "fail" if hi<limit else "indeterminate"
    else:
        status="pass" if hi<=limit else "fail" if lo>limit else "indeterminate"
    return {"gate":name,"status":status,"direction":direction,"threshold_bps":threshold,
            "identified_bounds":bounds(lo,hi)}


def analyze(enrolled,observations,thresholds):
    """Inputs come from the validated canonical cohort scoring path.

    Team membership is declared in the frozen roster. Teams have equal
    weight in one sensitivity view; no individual or team IDs are emitted.
    Leave-one-team-out extrema are descriptive finite-population stress tests.
    """
    p=policy(thresholds)
    if not enrolled:raise ValueError("empty enrolled roster")
    groups=defaultdict(list)
    for eid,row in enrolled.items():groups[row["team_id"]].append(eid)
    cells={}
    for row in observations:
        key=(row["episode_id"],row["arm"])
        if key in cells or key[0] not in enrolled or key[1] not in ARMS:
            raise ValueError("unplanned or duplicate observation")
        cells[key]=row
    ids=sorted(enrolled);teams=list(groups.values());n=len(ids)
    complete=sum(all(cells.get((eid,arm),{}).get("status")=="reviewed" for arm in ARMS) for eid in ids)
    per_team_complete=[sum(all(cells.get((eid,arm),{}).get("status")=="reviewed" for arm in ARMS)
                           for eid in group) for group in teams]
    gates=[]
    for name,actual,limit in (
        ("minimum_enrolled",n,p["min_enrolled"]),("minimum_teams",len(teams),p["min_teams"]),
        ("minimum_episodes_per_team",min(map(len,teams)),p["min_episodes_per_team"])):
        gates.append({"gate":name,"status":"pass" if actual>=limit else "fail",
                      "actual":actual,"minimum":limit})
    gates.append({"gate":"full_paired_coverage","status":"pass" if complete==n else "indeterminate",
                  "complete_paired_episodes":complete,"enrolled":n})
    rates={}
    for arm in ARMS:
        rates[arm]={}
        for field in OUTCOMES:
            lo,hi=interval(ids,cells,None,arm,field)
            rates[arm][field]=bounds(Fraction(lo,n),Fraction(hi,n))
            if arm=="goal":
                gate="min_goal_success_bps" if field=="continuation_success" else "max_goal_wrong_action_bps"
                gates.append(decision_check(gate,Fraction(lo,n),Fraction(hi,n),p[gate],
                                            "min" if field=="continuation_success" else "max"))
    contrasts={}
    for left,right in COMPARISONS:
        label=right+"_vs_"+left
        contrasts[label]={}
        for field in OUTCOMES:
            team_intervals=[interval(group,cells,left,right,field) for group in teams]
            lo=sum(a for a,b in team_intervals);hi=sum(b for a,b in team_intervals)
            micro_lo,micro_hi=Fraction(lo,n),Fraction(hi,n)
            macro_lo=sum((Fraction(a,len(g)) for (a,b),g in zip(team_intervals,teams)),Fraction())/len(teams)
            macro_hi=sum((Fraction(b,len(g)) for (a,b),g in zip(team_intervals,teams)),Fraction())/len(teams)
            dropped=[(Fraction(lo-a,n-len(g)),Fraction(hi-b,n-len(g)))
                     for (a,b),g in zip(team_intervals,teams) if len(g)<n]
            loo=(min(a for a,b in dropped),max(b for a,b in dropped)) if dropped else None
            contrasts[label][field]={
                "episode_weighted":bounds(micro_lo,micro_hi),
                "equal_team_weighted":bounds(macro_lo,macro_hi),
                "leave_one_team_out_envelope":bounds(*loo) if loo else None,
                "leave_one_team_out_views":len(dropped)}
            if right=="goal":
                good=field=="continuation_success"
                gate="min_goal_success_delta_bps" if good else "max_goal_wrong_action_delta_bps"
                direction="min" if good else "max"
                gates.append(decision_check(label+"_episode_"+gate,micro_lo,micro_hi,p[gate],direction))
                gates.append(decision_check(label+"_equal_team_"+gate,macro_lo,macro_hi,p[gate],direction))
                stress="min_leave_one_team_success_delta_bps" if good else "max_leave_one_team_wrong_action_delta_bps"
                gates.append(decision_check(label+"_leave_one_team_"+stress,*loo,p[stress],direction) if loo else
                             {"gate":label+"_leave_one_team_"+stress,"status":"indeterminate",
                              "direction":direction,"threshold_bps":p[stress],"identified_bounds":None})
    overall="fail" if any(g["status"]=="fail" for g in gates) else (
        "indeterminate" if any(g["status"]=="indeterminate" for g in gates) else "pass")
    return {"enrolled":n,"teams":len(teams),"complete_paired_episodes":complete,
            "minimum_team_enrolled":min(map(len,teams)),"maximum_team_enrolled":max(map(len,teams)),
            "minimum_team_complete_paired":min(per_team_complete),
            "rates":rates,"contrasts":contrasts,"gates":gates,"threshold_outcome":overall,
            "all_thresholds_met":overall=="pass","full_paired_coverage":complete==n,
            "confidence_intervals_computed":False,"causal_effect_verified":False,
            "product_effectiveness_verified":False,"rollout_authorized":False}
