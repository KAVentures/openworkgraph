"""Independent exact portfolio frontier and membership sensitivity oracles."""
from decimal import Decimal
from itertools import combinations
import random

import pytest

from server import automation_portfolio_frontier as f
from server import automation_budget_scenario as s
from server import automation_pipeline as p
from test_automation_budget_scenario import store,idea,request


def key(row):
    ids,cost,points=row
    return -points,cost,len(ids),ids


def unpack(row):
    if row is None:return None
    return tuple(row["selected_idea_ids"]),Decimal(row["budget_used"]),row["priority_points"]


def test_independent_randomized_exhaustive_frontier_oracle():
    rng=random.Random(1559)
    for trial in range(160):
        n=rng.randint(1,8)
        ids=[f"i{i}" for i in range(n)]
        costs=[Decimal(rng.randrange(0,20))/10 for _ in ids]
        points=[rng.randrange(0,9) for _ in ids]
        maximum=rng.randint(1,n)
        required={i for i in range(n) if rng.random()<.12}
        blocked={i for i in range(n) if rng.random()<.1}
        deps=[{j for j in range(i) if rng.random()<.2} for i in range(n)]
        cap=Decimal(rng.randrange(0,60))/10
        targets=[0,5,15,99]
        checkpoints=[0,1.5,4,10]
        engine=f.Frontier(ids,cap,targets,checkpoints)
        structural=[]
        for size in range(maximum+1):
            for subset in combinations(range(n),size):
                chosen=set(subset)
                if not required<=chosen or chosen&blocked or any(not deps[i]<=chosen for i in chosen):continue
                row=(tuple(ids[i] for i in subset),sum((costs[i] for i in subset),Decimal(0)),sum(points[i] for i in subset))
                structural.append(row)
                engine.visit(sum(1<<i for i in subset),row[1],row[2])
        result=engine.report()
        feasible=sorted((r for r in structural if r[1]<=cap),key=key)
        assert result["structurally_feasible_subsets"]==len(structural)
        assert result["current_budget_feasible_subsets"]==len(feasible)
        # Compare objective pairs via direct pairwise dominance, with no
        # shared frontier sorting algorithm.
        expected={(r[1],r[2]) for r in structural if not any(
            x[1]<=r[1] and x[2]>=r[2] and (x[1]<r[1] or x[2]>r[2]) for x in structural)}
        assert {(Decimal(r["budget_used"]),r["priority_points"]) for r in result["frontier"]}==expected
        for row in result["frontier"]:
            a=unpack(row)
            assert a==min((r for r in structural if r[1:]==a[1:]),key=key)
        assert unpack(result["ranked_optimum"])==(feasible[0] if feasible else None)
        optimal=[r for r in feasible if r[2]==feasible[0][2]] if feasible else []
        assert result["maximum_priority_portfolios"]==len(optimal)
        assert result["maximum_priority_at_current_budget"]==(feasible[0][2] if feasible else None)
        for t in result["priority_targets"]:
            candidates=[r for r in structural if r[2]>=t["minimum_priority_points"]]
            expected_row=min(candidates,key=lambda r:(r[1],*key(r))) if candidates else None
            assert unpack(t["least_cost_portfolio"])==expected_row
        for c in result["budget_checkpoints"]:
            budget=Decimal(c["budget_limit"])
            under=[r for r in structural if r[1]<=budget]
            best=min(under,key=key) if under else None
            assert unpack(c["best_portfolio"])==best
            greater=[r for r in structural if best is None or r[2]>best[2]]
            next_cost=min((r[1] for r in greater),default=None)
            assert (Decimal(c["additional_budget_to_next_breakpoint"]) if c["additional_budget_to_next_breakpoint"] is not None else None)==(next_cost-budget if next_cost is not None else None)
        for i,row in enumerate(result["candidate_sensitivity"]):
            ident=ids[i]
            included=[r for r in feasible if ident in r[0]]
            excluded=[r for r in feasible if ident not in r[0]]
            assert row["feasible_subsets_including"]==len(included)
            assert row["feasible_subsets_excluding"]==len(excluded)
            assert unpack(row["forced_in"]["best_portfolio"])==(included[0] if included else None)
            assert unpack(row["forced_out"]["best_portfolio"])==(excluded[0] if excluded else None)
            all_in=[r for r in structural if ident in r[0]]
            all_out=[r for r in structural if ident not in r[0]]
            assert row["structural_subsets_including"]==len(all_in)
            assert row["structural_subsets_excluding"]==len(all_out)
            for name,subset in [("including",all_in),("excluding",all_out)]:
                expected_min=min(subset,key=lambda r:(r[1],*key(r))) if subset else None
                assert unpack(row["least_cost_portfolio_"+name])==expected_min
            flag="no_feasible_portfolio" if not optimal else "all" if all(ident in r[0] for r in optimal) else "some" if any(ident in r[0] for r in optimal) else "none"
            assert row["membership_among_max_priority_portfolios"]==flag


def test_integrated_dependency_budget_frontier_and_checkpoints():
    a=idea("Prerequisite",10);b=idea("Dependent",20,[a]);c=idea("Alternative",25)
    spec=request([(a,0),(b,100),(c,70)],limit=25,
                 frontier_review={"priority_targets":[0,70,100,170,171],"budget_checkpoints":[0,25,30,55]})
    r=s.solve(spec);f=r["frontier_review"]
    assert f["structurally_feasible_subsets"]==6 and f["current_budget_feasible_subsets"]==3
    assert [(Decimal(x["budget_used"]),x["priority_points"]) for x in f["frontier"]]==[(0,0),(25,70),(30,100),(55,170)]
    targets=f["priority_targets"]
    assert Decimal(targets[2]["least_cost_portfolio"]["additional_budget_required"])==5
    assert targets[-1]["least_cost_portfolio"] is None
    assert [x["best_portfolio"]["priority_points"] for x in f["budget_checkpoints"]]==[0,70,100,170]
    assert Decimal(f["budget_checkpoints"][1]["additional_budget_to_next_breakpoint"])==5
    rows={x["idea_id"]:x for x in f["candidate_sensitivity"]}
    assert rows[b["idea_id"]]["forced_in"]["best_portfolio"] is None
    assert Decimal(rows[b["idea_id"]]["least_cost_portfolio_including"]["budget_used"])==30
    assert rows[c["idea_id"]]["forced_out"]["priority_loss_vs_optimum"]==70
    base=s.solve({k:v for k,v in spec.items() if k!="frontier_review"})
    assert base["alternatives"]==r["alternatives"] and base["feasible_subsets"]==r["feasible_subsets"]
    assert "frontier_review" not in base


def test_ties_not_confused_with_ranked_optimum():
    a=idea("A",10);b=idea("B",20)
    r=s.solve(request([(a,5),(b,5)],limit=30,max_selected=1,
                      frontier_review={"priority_targets":[5]}))["frontier_review"]
    assert r["maximum_priority_portfolios"]==2
    assert Decimal(r["maximum_priority_portfolios_minimum_cost"])==10
    assert Decimal(r["maximum_priority_portfolios_maximum_cost"])==20
    assert all(x["membership_among_max_priority_portfolios"]=="some" for x in r["candidate_sensitivity"])
    assert r["ranked_optimum"]["selected_idea_ids"]==[a["idea_id"]]


def test_infeasible_current_budget_has_structural_frontier():
    a=idea("Required",10)
    r=s.solve(request([(a,50)],limit=0,required=[a["idea_id"]],
                      frontier_review={"priority_targets":[0,50,51]}))["frontier_review"]
    assert r["ranked_optimum"] is None and r["current_budget_feasible_subsets"]==0
    assert r["structurally_feasible_subsets"]==1
    assert Decimal(r["priority_targets"][0]["least_cost_portfolio"]["additional_budget_required"])==10
    assert r["priority_targets"][2]["least_cost_portfolio"] is None


@pytest.mark.parametrize("value",[{"priority_targets":[]},{"priority_targets":[True]},
    {"priority_targets":[1801]},{"priority_targets":[0,0]},{"priority_targets":[1],"budget_checkpoints":[True]},
    {"priority_targets":[1],"budget_checkpoints":[1,1.0]},{"priority_targets":[1],"budget_checkpoints":[-1]},
    {"priority_targets":[1],"extra":True}])
def test_bad_options(value):
    a=idea("A",1)
    with pytest.raises(p.PipelineError):s.solve(request([(a,1)],frontier_review=value))


def test_changed_snapshot_refused_after_enumeration(monkeypatch):
    a=idea("A",1)
    spec=request([(a,1)],frontier_review={"priority_targets":[1]})
    original=s._snapshot
    calls=0
    def changed():
        nonlocal calls
        calls+=1
        snapshot=original()
        if calls==2:snapshot["snapshot_sha256"]="0"*64
        return snapshot
    monkeypatch.setattr(s,"_snapshot",changed)
    with pytest.raises(p.PipelineError,match="changed during search"):s.solve(spec)
    monkeypatch.setattr(s,"_snapshot",original)
    assert s.solve(spec)["frontier_review"]["maximum_priority_at_current_budget"]==1


def test_guard_releases_and_refuses_parallel_search():
    a=idea("A",1)
    spec=request([(a,1)])
    assert f.GUARD.acquire(blocking=False)
    try:
        with pytest.raises(p.PipelineError,match="another local portfolio"):s.solve(spec)
    finally:f.GUARD.release()
    assert s.solve(spec)["search_complete"]


def test_stakeholder_robustness_exact_independent_oracle():
    from server.automation_portfolio_preferences import Profile
    rng=random.Random(1560)
    for _ in range(120):
        n=rng.randint(1,8)
        ids=[f"i{i}" for i in range(n)]
        costs=[Decimal(rng.randrange(1,12)) for _ in ids]
        weights=[[rng.randrange(0,101) for _ in ids] for _ in range(3)]
        cap=Decimal(rng.randrange(1,24))
        profiles=[Profile(label=f"Profile {i}",priorities=dict(zip(ids,w))) for i,w in enumerate(weights[1:])]
        engine=f.Frontier(ids,cap,[0],profiles=profiles)
        population=[]
        for k in range(n+1):
            for subset in combinations(range(n),k):
                cost=sum((costs[i] for i in subset),Decimal(0))
                scores=tuple(sum(w[i] for i in subset) for w in weights)
                engine.visit(sum(1<<i for i in subset),cost,scores[0])
                if cost<=cap:population.append((tuple(ids[i] for i in subset),cost,scores))
        result=engine.report()["preference_review"]
        ideal=tuple(max(x[2][i] for x in population) for i in range(3))
        def regrets(x):return tuple(a-b for a,b in zip(ideal,x[2]))
        def secondary(x):return x[1],len(x[0]),x[0]
        minimax=min(population,key=lambda x:(max(regrets(x)),sum(regrets(x)),*secondary(x)))
        maximin=min(population,key=lambda x:(-min(x[2]),-sum(x[2]),*secondary(x)))
        for label,expected in [("minimax_regret_portfolio",minimax),("maximin_priority_portfolio",maximin)]:
            row=result[label]
            assert tuple(row["selected_idea_ids"])==expected[0]
            assert Decimal(row["budget_used"])==expected[1]
            assert tuple(s["priority_points"] for s in row["profile_scores"])==expected[2]
            assert row["maximum_regret_points"]==max(regrets(expected))
        assert result["simultaneously_optimal_portfolios"]==sum(x[2]==ideal for x in population)
        assert [x["ideal_priority_points"] for x in result["individual_optima"]]==list(ideal)


def test_integrated_conflicting_preferences_and_compromise():
    a=idea("A",10);b=idea("B",10);c=idea("Compromise",10)
    r=s.solve(request([(a,100),(b,0),(c,60)],limit=10,frontier_review={
        "priority_targets":[60],"preference_profiles":[{"label":"Other team",
        "priorities":{a["idea_id"]:0,b["idea_id"]:100,c["idea_id"]:60}}]}))["frontier_review"]["preference_review"]
    assert r["minimax_regret_portfolio"]["selected_idea_ids"]==[c["idea_id"]]
    assert r["maximin_priority_portfolio"]["selected_idea_ids"]==[c["idea_id"]]
    assert r["minimax_regret_portfolio"]["maximum_regret_points"]==40
    assert r["simultaneously_optimal_portfolios"]==0


def test_bad_preference_profiles_and_empty_feasible_population():
    a=idea("A",10)
    ident=a["idea_id"]
    for profiles in [[{"label":"Team","priorities":{}}],
                     [{"label":"Team","priorities":{ident:True}}],
                     [{"label":"Team","priorities":{ident:101}}],
                     [{"label":"Original priority","priorities":{ident:50}}],
                     [{"label":"Team","priorities":{ident:10}},{"label":"team","priorities":{ident:20}}],
                     [{"label":"Team","priorities":{ident:10,"foreign":20}}]]:
        with pytest.raises(p.PipelineError):
            s.solve(request([(a,10)],frontier_review={"priority_targets":[10],"preference_profiles":profiles}))
    r=s.solve(request([(a,10)],limit=0,required=[ident],frontier_review={"priority_targets":[10],
        "preference_profiles":[{"label":"Team","priorities":{ident:20}}]}))["frontier_review"]["preference_review"]
    assert r["status"]=="no_feasible_portfolio"
    assert r["minimax_regret_portfolio"] is None
