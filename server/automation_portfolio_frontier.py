"""Exact cost/priority frontier, budget target inversion and membership sensitivity."""
from collections import Counter
from decimal import Decimal
from threading import BoundedSemaphore
from pydantic import BaseModel, ConfigDict, Field

GUARD=BoundedSemaphore(1)

class FrontierSpec(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    priority_targets: list[int]=Field(min_length=1,max_length=12)

def check(spec):
    targets=spec.priority_targets
    if any(not 0<=p<=1800 for p in targets) or len(set(targets))!=len(targets):
        raise ValueError("choose distinct integer priority targets from 0 to 1800")

class Frontier:
    def __init__(self,ids,cap,targets):
        self.ids=ids
        self.cap=cap
        self.targets=sorted(targets)
        self.by_points={}
        self.feasible=0
        self.structural_count=0
        self.inside=[None]*len(ids)
        self.outside=[None]*len(ids)
        self.membership_counts=[0]*len(ids)
        self.optimum_points=None
        self.optimum_count=0
        self.optimum_any=0
        self.optimum_all=0
        self.optimum_min_cost=None
        self.optimum_max_cost=None
        self.best=None
        self.counts=Counter()

    def key(self,row):
        mask,cost,points=row
        return (-points,cost,mask.bit_count(),tuple(k for i,k in enumerate(self.ids) if mask&(1<<i)))

    def better(self,a,b):
        if b is None:return True
        # Avoid materializing ID tuples for unequal score/cost/count.
        x=(-a[2],a[1],a[0].bit_count())
        y=(-b[2],b[1],b[0].bit_count())
        return x<y if x!=y else self.key(a)<self.key(b)

    def visit(self,mask,cost,points):
        row=(mask,cost,points)
        self.structural_count+=1
        if self.better(row,self.by_points.get(points)):
            self.by_points[points]=row
        if cost>self.cap:return
        self.feasible+=1
        self.counts[mask.bit_count()]+=1
        if self.better(row,self.best):self.best=row
        for i in range(len(self.ids)):
            included=bool(mask&(1<<i))
            if included:self.membership_counts[i]+=1
            side=self.inside if included else self.outside
            if self.better(row,side[i]):side[i]=row
        if self.optimum_points is None or points>self.optimum_points:
            self.optimum_points=points
            self.optimum_count=1
            self.optimum_any=self.optimum_all=mask
            self.optimum_min_cost=self.optimum_max_cost=cost
        elif points==self.optimum_points:
            self.optimum_count+=1
            self.optimum_any|=mask
            self.optimum_all&=mask
            self.optimum_min_cost=min(self.optimum_min_cost,cost)
            self.optimum_max_cost=max(self.optimum_max_cost,cost)

    @staticmethod
    def money(value):
        return format(value,"f") if value is not None else None

    def packet(self,row):
        if row is None:return None
        mask,cost,points=row
        return {"selected_idea_ids":[key for i,key in enumerate(self.ids) if mask&(1<<i)],
                "selected_count":mask.bit_count(),"priority_points":points,
                "budget_used":self.money(cost),"within_current_budget":cost<=self.cap,
                "additional_budget_required":self.money(max(Decimal(0),cost-self.cap))}

    def report(self):
        # Descending points; equal-cost lower scores are dominated.
        frontier=[]
        cheapest=None
        for points in sorted(self.by_points,reverse=True):
            row=self.by_points[points]
            cost=row[1]
            if cheapest is None or cost<cheapest:
                frontier.append(row);cheapest=cost
        frontier.reverse()
        targets=[]
        for points in self.targets:
            eligible=[r for r in self.by_points.values() if r[2]>=points]
            row=min(eligible,key=lambda r:(r[1],-r[2],r[0].bit_count(),self.key(r)[-1])) if eligible else None
            targets.append({"minimum_priority_points":points,"possible_under_structural_constraints":row is not None,
                            "least_cost_portfolio":self.packet(row)})
        sensitivity=[]
        for i,ident in enumerate(self.ids):
            inc,exc=self.inside[i],self.outside[i]
            def side(row):
                return {"best_portfolio":self.packet(row),
                        "priority_loss_vs_optimum":self.best[2]-row[2] if row is not None and self.best is not None else None,
                        "budget_delta_vs_ranked_optimum":self.money(row[1]-self.best[1]) if row is not None and self.best is not None else None}
            sensitivity.append({"idea_id":ident,"feasible_subsets_including":self.membership_counts[i],
                                "feasible_subsets_excluding":self.feasible-self.membership_counts[i],
                                "forced_in":side(inc),"forced_out":side(exc),
                                "membership_among_max_priority_portfolios":("no_feasible_portfolio" if self.best is None else
                                    "all" if self.optimum_all&(1<<i) else "some" if self.optimum_any&(1<<i) else "none")})
        return {"format":"openworkgraph.portfolio-budget-frontier.v1","structurally_feasible_subsets":self.structural_count,
                "current_budget_feasible_subsets":self.feasible,"frontier_complete":True,
                "frontier":[self.packet(r) for r in frontier],"priority_targets":targets,"candidate_sensitivity":sensitivity,
                "feasible_subset_counts_by_size":[{"selected_count":k,"count":v} for k,v in sorted(self.counts.items())],
                "maximum_priority_at_current_budget":self.optimum_points,
                "maximum_priority_portfolios":self.optimum_count,
                "maximum_priority_portfolios_minimum_cost":self.money(self.optimum_min_cost),
                "maximum_priority_portfolios_maximum_cost":self.money(self.optimum_max_cost),
                "ranked_optimum":self.packet(self.best),
                "interpretation":"The exact frontier includes all structurally feasible subsets, including costs above the current cap. "
                    "Each point represents one deterministic portfolio; equal-priority/equal-cost alternatives can exist. "
                    "Membership counts and forced inclusion/exclusion use the current budget. Priority points are declared preferences, not financial benefits. "
                    "No approval, resource feasibility or execution is implied."}
