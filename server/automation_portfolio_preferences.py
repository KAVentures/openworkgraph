"""Exact multi-stakeholder preference sensitivity for one bounded feasible population."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MAX_PROFILE_CANDIDATES=16
MAX_PROFILES=3

class Profile(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    label: str=Field(min_length=1,max_length=80)
    priorities: dict[str,int]=Field(min_length=1,max_length=MAX_PROFILE_CANDIDATES)


def validate(profiles,ids):
    if profiles and len(ids)>MAX_PROFILE_CANDIDATES:
        raise ValueError("multi-profile comparison supports at most 16 candidates")
    seen={"original priority"}
    for p in profiles:
        if p.label.strip()!=p.label or not p.label.isprintable() or p.label.casefold() in seen:
            raise ValueError("preference profiles require distinct printable labels")
        seen.add(p.label.casefold())
        if set(p.priorities)!=set(ids) or any(not 0<=v<=100 for v in p.priorities.values()):
            raise ValueError("each profile requires an integer score from 0 to 100 for every selected candidate")


class PreferenceReview:
    def __init__(self,frontier,profiles):
        self.frontier=frontier
        self.labels=["Original priority",*(p.label for p in profiles)]
        self.scores=[tuple(p.priorities[k] for k in frontier.ids) for p in profiles]
        self.records=[]
        self.best=[None]*len(self.labels)

    def visit(self,mask,cost,points):
        indices=[i for i in range(len(self.frontier.ids)) if mask&(1<<i)]
        scores=(points,*(sum(s[i] for i in indices) for s in self.scores))
        row=(mask,cost,scores)
        self.records.append(row)
        for i,score in enumerate(scores):
            old=self.best[i]
            if old is None or self._rank(row,i)<self._rank(old,i):self.best[i]=row

    def _secondary(self,row):
        mask,cost,_=row
        return cost,mask.bit_count(),tuple(k for i,k in enumerate(self.frontier.ids) if mask&(1<<i))

    def _rank(self,row,i):
        return (-row[2][i],*self._secondary(row))

    def packet(self,row,ideals):
        if row is None:return None
        mask,cost,scores=row
        regrets=[a-b for a,b in zip(ideals,scores)]
        return {**self.frontier.packet((mask,cost,scores[0])),
                "profile_scores":[{"label":label,"priority_points":score,"regret_points":regret}
                    for label,score,regret in zip(self.labels,scores,regrets)],
                "worst_profile_priority_points":min(scores),
                "maximum_regret_points":max(regrets),"total_regret_points":sum(regrets)}

    def report(self):
        if not self.records:
            return {"status":"no_feasible_portfolio","profiles":self.labels,"feasible_subsets":0,
                    "individual_optima":[],"minimax_regret_portfolio":None,"maximin_priority_portfolio":None,
                    "simultaneously_optimal_portfolios":0,"robustness_verified_over_declared_profiles":True,
                    "future_preferences_verified":False}
        ideals=[r[2][i] for i,r in enumerate(self.best)]
        def regret_key(r):
            regret=[a-b for a,b in zip(ideals,r[2])]
            return max(regret),sum(regret),*self._secondary(r)
        def maximin_key(r):
            return -min(r[2]),-sum(r[2]),*self._secondary(r)
        minimax=min(self.records,key=regret_key)
        maximin=min(self.records,key=maximin_key)
        common=sum(tuple(r[2])==tuple(ideals) for r in self.records)
        return {"status":"feasible","profiles":self.labels,"feasible_subsets":len(self.records),
                "individual_optima":[{"label":label,"ideal_priority_points":ideals[i],"portfolio":self.packet(r,ideals)}
                    for i,(label,r) in enumerate(zip(self.labels,self.best))],
                "minimax_regret_portfolio":self.packet(minimax,ideals),
                "maximin_priority_portfolio":self.packet(maximin,ideals),
                "simultaneously_optimal_portfolios":common,
                "robustness_verified_over_declared_profiles":True,"future_preferences_verified":False,
                "interpretation":"All preference profiles use the same eligible subsets, budget and declared point scale. "
                    "Regret is the gap from that profile's own optimum. Minimax minimizes maximum regret, then total regret, cost, count and IDs. "
                    "Maximin maximizes worst score, then summed scores, then minimizes cost/count/IDs. These are explicit preference scenarios, not measured business benefit or predictions."}
