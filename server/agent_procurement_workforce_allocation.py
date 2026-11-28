"""Exact bounded cross-workflow portfolio allocation for operator review.

Recomputed operating-cost scenarios, not actual budgets or release permission.
One alternative or deferral per workflow; no shared discounts or fungible
labor assumption. All chosen AI costs are charged gross, not netted against
hypothetical savings.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import product
from math import prod
from typing import Annotated

from pydantic import Field, StrictBool, StrictInt, model_validator

from shared.agent_procurement import Alias, Strict, digest
from shared.agent_procurement_operating_cost import operation
from shared.agent_procurement_portfolio import _pair, _read
from shared.agent_procurement_router_rehearsal import _money_bounds, _rational
from shared.process_shadow_review import Hash
from .local_process_catalog import catalog

MAX_COMBINATIONS=200_000
Limit=Annotated[StrictInt,Field(ge=0,le=10**18)]
WorkflowID=Annotated[str,Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
Currency=Annotated[str,Field(pattern=r"^[A-Z]{3}$")]


class Workflow(Strict):
    workflow_id: WorkflowID
    revision: Annotated[StrictInt,Field(ge=1,le=2**31-1)]
    expected_operating_report_sha256: Hash
    operating_bundle: dict
    portfolio_ids: list[Alias]=Field(min_length=1,max_length=8)

    @model_validator(mode="after")
    def unique(self):
        if len(set(self.portfolio_ids))!=len(self.portfolio_ids):
            raise ValueError("duplicate requested portfolio")
        return self


class Request(Strict):
    currency: Currency
    maximum_first_year_gross_ai_cost_microunits: Limit
    maximum_annual_ai_cases: Limit
    maximum_annual_additional_person_ms: Limit
    confirm_non_overlapping_workloads_and_costs: StrictBool
    confirmed: StrictBool
    workflows: list[Workflow]=Field(min_length=1,max_length=8)

    @model_validator(mode="after")
    def unique(self):
        if not self.confirmed or not self.confirm_non_overlapping_workloads_and_costs:
            raise ValueError("explicit accounting and workload confirmation required")
        if len({w.workflow_id for w in self.workflows})!=len(self.workflows):
            raise ValueError("one revision per workflow required")
        return self


def _option(p):
    reasons=[]
    if p["declared_evidence_status"]!="pass":reasons.append("outcome_or_trial_gate")
    saved=_pair(p["first_year_savings"]);capacity=_read(p["annual_released_person_ms"]["lower"])
    if saved[0] is None or saved[1] is None or not p["financial_evidence_complete"]:
        reasons.append("incomplete_financial_evidence")
    elif saved[0]<=0:reasons.append("nonpositive_guaranteed_scenario_savings")
    if capacity is None or not p["capacity_evidence_complete"]:
        reasons.append("incomplete_capacity_evidence")
    if p["ai_cases"]<=0:reasons.append("no_ai_cases")
    gross=Fraction(int(p["candidate_and_routing_annual_fixed_microunits"])+
                   int(p["candidate_and_routing_one_time_microunits"]))+_read(p["routing_variable_cost"])
    for route in p["routes"]:
        if route["candidate_id"] is not None and route["annual_cases"]:
            amount=_pair(route["annual_variable_cost"])[1]
            if amount is None:gross=None;break
            gross+=amount
    if gross is None:reasons.append("unbounded_gross_ai_cost")
    return {"id":p["portfolio_id"],"eligible":not reasons,"reasons":reasons,
        "saving":saved[0],"gross":gross,"ai_cases":p["ai_cases"],
        "additional":max(Fraction(0),-capacity) if capacity is not None else None,
        "released":capacity}


def _defer():
    return {"id":None,"eligible":True,"reasons":[],"saving":Fraction(0),
            "gross":Fraction(0),"ai_cases":0,"additional":Fraction(0),"released":Fraction(0)}


def solve(groups,limits):
    """Enumerate finite exact choices; fail closed above the explicit bound.

    Maximize total lower-bound first-year savings, then minimize gross cost,
    additional person-time, AI cases, and finally choose lexicographic IDs.
    Deferral consumes no incremental envelope and has zero scenario savings.
    """
    choices=[[x for x in group if x["eligible"]] for group in groups]
    count=prod(len(g) for g in choices)
    if count>MAX_COMBINATIONS:
        raise ValueError("too many eligible combinations; reduce alternatives")
    best=None;feasible=0
    for selection in product(*choices):
        saving=sum(x["saving"] for x in selection)
        gross=sum(x["gross"] for x in selection)
        cases=sum(x["ai_cases"] for x in selection)
        additional=sum(x["additional"] for x in selection)
        if gross>limits[0] or cases>limits[1] or additional>limits[2]:
            continue
        feasible+=1
        score=(-saving,gross,additional,cases,tuple(x["id"] or "" for x in selection))
        if best is None or score<best[0]:
            best=(score,selection)
    if best is None:raise ValueError("no feasible allocation")
    return best[1],count,feasible


def _amount(x):
    return _money_bounds((x,x))["lower_microunits"]


def _public(o):
    return {"portfolio_id":o["id"],"eligible":o["eligible"],"exclusion_reasons":o["reasons"],
        "first_year_savings_lower_microunits":_amount(o["saving"]),
        "first_year_gross_ai_cost_upper_microunits":_amount(o["gross"]),
        "annual_ai_cases":o["ai_cases"],
        "annual_additional_person_ms_upper":_amount(o["additional"]),
        "annual_released_person_ms_lower":_amount(o["released"])}


def assess(document):
    req=Request.model_validate(document)
    rows=[];groups=[];seen=set()
    for w in sorted(req.workflows,key=lambda x:x.workflow_id):
        current=catalog.get(w.workflow_id)
        if current["revision"]!=w.revision:raise ValueError("stale workflow revision")
        packet=catalog.export(w.workflow_id,w.revision)
        report=operation(packet,w.operating_bundle)
        if report["report_sha256"]!=w.expected_operating_report_sha256 or report["currency"]!=req.currency:
            raise ValueError("operating report or currency changed")
        # Same exact input hashes may represent overlapping workloads; do not
        # count them twice, even if a customer declares independent volumes.
        hashes={x["case_sha256"] for x in w.operating_bundle["plan"]["basis"]["reference_report"]["cases"]}
        if seen & hashes:raise ValueError("overlapping declared evaluation case hashes")
        seen.update(hashes)
        portfolios={p["portfolio_id"]:p for p in report.get("conditional_portfolios",{}).get("portfolios",[])}
        if set(w.portfolio_ids)-set(portfolios):raise ValueError("unknown requested portfolio")
        options=[_defer(),*[_option(portfolios[k]) for k in sorted(w.portfolio_ids)]]
        groups.append(options)
        rows.append({"workflow_id":w.workflow_id,"revision":w.revision,"title":current["title"],
            "operating_report_sha256":report["report_sha256"],"options":[_public(o) for o in options]})
    limits=(req.maximum_first_year_gross_ai_cost_microunits,req.maximum_annual_ai_cases,
            req.maximum_annual_additional_person_ms)
    selection,count,feasible=solve(groups,limits)
    for row,o in zip(rows,selection):row["selected_for_review"]=_public(o)
    for w in req.workflows:
        if catalog.get(w.workflow_id)["revision"]!=w.revision:raise ValueError("workflow revision changed during review")
    gross=sum(x["gross"] for x in selection);cases=sum(x["ai_cases"] for x in selection)
    extra=sum(x["additional"] for x in selection);saving=sum(x["saving"] for x in selection)
    result={"format":"openworkgraph.procurement-workforce-allocation.v1","currency":req.currency,
        "request_sha256":digest(req.model_dump()),"evaluated_combinations":count,"feasible_combinations":feasible,
        "optimal_within_enumerated_scenarios":True,"rows":rows,
        "first_year_savings_lower_microunits":_amount(saving),"first_year_gross_ai_cost_upper_microunits":_amount(gross),
        "annual_ai_cases":cases,"annual_additional_person_ms_upper":_amount(extra),
        "gross_ai_cost_headroom_microunits":_amount(limits[0]-gross),"ai_case_headroom":str(limits[1]-cases),
        "additional_person_ms_headroom":_amount(limits[2]-extra),
        "selected_workflows":sum(x["id"] is not None for x in selection),
        "source_outcomes_verified":False,"realized_savings_verified":False,
        "spending_authorized":False,"execution_authorized":False,"result_persisted":False,
        "limitations":["Explicit selected saved workflows and conditional operating portfolios only; no enterprise-wide inventory.",
            "Gross AI envelope includes assigned AI variable costs, routing and candidate fixed/one-time costs; excludes retained manual paths and is not total company spend.",
            "Additional person-time charges each workflow's worst-case increase; no offsets from another workflow's released time or shared discounts are assumed.",
            "Deferral means no proposed change; zero incremental cost/savings is not a claim about manual baseline safety or cost.",
            "Exact input hash overlaps are refused; distinct hashes do not establish independent business volumes.",
            "Outcome gates and costs are declared scenario evidence, not real-world verified outcomes or production routing readiness.",
            "Exact optimum applies only to supplied finite alternatives and assumptions; no purchasing or deployment occurs."]}
    return {**result,"report_sha256":digest(result)}
